mod capture;
mod input;
mod protocol;
mod uia;
mod win_utils;

use protocol::{read_request, write_response, ProtocolError, Response, PROTOCOL_VERSION};
use serde_json::json;
use std::io::{stdin, stdout, BufReader, BufWriter};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;

use windows::Graphics::Capture::GraphicsCaptureSession;
use windows::Win32::Graphics::Direct3D::{D3D_DRIVER_TYPE_HARDWARE, D3D_FEATURE_LEVEL_11_0};
use windows::Win32::Graphics::Direct3D11::{
    D3D11CreateDevice, ID3D11Device, ID3D11DeviceContext, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
    D3D11_SDK_VERSION,
};
use windows::Win32::System::Com::{CoInitializeEx, CoUninitialize, COINIT_MULTITHREADED};
use windows::Win32::System::Threading::GetCurrentProcess;
use windows::Win32::UI::HiDpi::{
    AreDpiAwarenessContextsEqual, GetDpiAwarenessContextForProcess,
    SetProcessDpiAwarenessContext, DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2,
};

#[allow(dead_code)]
struct AttachedTarget {
    hwnd: String,
    hwnd_num: usize,
    pid: u32,
    create_time: String,
    geometry_epoch: Arc<AtomicU64>,
    foreground_epoch: Arc<AtomicU64>,
}

fn run_doctor() -> serde_json::Value {
    let desktop_status = match win_utils::check_interactive_desktop() {
        Ok(msg) => json!({ "accessible": true, "details": msg }),
        Err(e) => json!({ "accessible": false, "error": e }),
    };

    let com_status = unsafe {
        let hr = CoInitializeEx(None, COINIT_MULTITHREADED);
        if hr.is_ok() {
            CoUninitialize();
            json!({ "supported": true })
        } else {
            json!({ "supported": false, "error": format!("{:?}", hr) })
        }
    };

    let d3d11_status = unsafe {
        let mut device: Option<ID3D11Device> = None;
        let mut context: Option<ID3D11DeviceContext> = None;
        let mut feature_level = D3D_FEATURE_LEVEL_11_0;

        let res = D3D11CreateDevice(
            None,
            D3D_DRIVER_TYPE_HARDWARE,
            None,
            D3D11_CREATE_DEVICE_BGRA_SUPPORT,
            Some(&[D3D_FEATURE_LEVEL_11_0]),
            D3D11_SDK_VERSION,
            Some(&mut device),
            Some(&mut feature_level),
            Some(&mut context),
        );

        match res {
            Ok(_) => json!({ "supported": true, "feature_level": "11.0" }),
            Err(e) => json!({ "supported": false, "error": e.message().to_string() }),
        }
    };

    let wgc_supported = match GraphicsCaptureSession::IsSupported() {
        Ok(b) => json!({ "supported": b }),
        Err(e) => json!({ "supported": false, "error": e.message().to_string() }),
    };

    let dpi_status = unsafe {
        let ctx = GetDpiAwarenessContextForProcess(GetCurrentProcess());
        let is_v2 = AreDpiAwarenessContextsEqual(ctx, DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
        json!({ "per_monitor_v2": is_v2.as_bool() })
    };

    json!({
        "engine_version": "0.1.0",
        "protocol_version": PROTOCOL_VERSION,
        "pid": std::process::id(),
        "desktop": desktop_status,
        "com": com_status,
        "d3d11": d3d11_status,
        "wgc": wgc_supported,
        "dpi": dpi_status,
    })
}

fn main() {
    let _ = win_utils::attach_thread_to_input_desktop();
    unsafe {
        let _ = SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
        let _ = CoInitializeEx(None, COINIT_MULTITHREADED);
    }

    let stdin = stdin();
    let stdout = stdout();
    let mut reader = BufReader::new(stdin.lock());
    let mut writer = BufWriter::new(stdout.lock());

    let running = Arc::new(AtomicBool::new(true));
    let mut attached: Option<AttachedTarget> = None;
    let mut last_observation: Option<input::LastObservation> = None;
    let capture_worker = capture::CaptureWorkerHandle::new();
    let uia_worker = uia::UiaWorkerHandle::new();

    while running.load(Ordering::SeqCst) {
        let (req, _payload) = match read_request(&mut reader) {
            Ok(Some((r, p))) => (r, p),
            Ok(None) => break, // EOF
            Err(err) => {
                let err_resp = Response {
                    v: PROTOCOL_VERSION,
                    id: 0,
                    ok: false,
                    result: serde_json::Value::Null,
                    error: Some(err),
                    payload_len: 0,
                };
                let _ = write_response(&mut writer, &err_resp, &[]);
                continue;
            }
        };

        let req_id = req.id;
        let mut resp = Response {
            v: PROTOCOL_VERSION,
            id: req_id,
            ok: true,
            result: serde_json::Value::Null,
            error: None,
            payload_len: 0,
        };
        let mut out_payload = Vec::new();

        match req.op.as_str() {
            "doctor" => {
                resp.result = run_doctor();
            }
            "list_windows" => {
                let windows = win_utils::list_windows();
                resp.result = json!({ "windows": windows });
            }
            "attach" => {
                let hwnd_str = match req.args.get("hwnd").and_then(|v| v.as_str()) {
                    Some(h) => h,
                    None => {
                        resp.ok = false;
                        resp.error = Some(ProtocolError::new("invalid_request", "Missing required string 'hwnd'"));
                        let _ = write_response(&mut writer, &resp, &[]);
                        continue;
                    }
                };

                let hwnd_num: usize = match hwnd_str.parse() {
                    Ok(n) => n,
                    Err(_) => {
                        resp.ok = false;
                        resp.error = Some(ProtocolError::new("invalid_request", "Invalid decimal hwnd"));
                        let _ = write_response(&mut writer, &resp, &[]);
                        continue;
                    }
                };

                let expected_pid = req.args.get("pid").and_then(|v| v.as_u64()).map(|v| v as u32);
                let expected_create_time = req.args.get("process_create_time_utc").and_then(|v| v.as_str()).unwrap_or("unknown");

                match expected_pid {
                    Some(pid) => {
                        match win_utils::validate_window_identity(hwnd_num, pid, expected_create_time) {
                            Ok(_) => {
                                let geometry_epoch = Arc::new(AtomicU64::new(1));
                                let foreground_epoch = Arc::new(AtomicU64::new(1));

                                match capture_worker.start_capture(
                                    hwnd_num,
                                    pid,
                                    expected_create_time.to_string(),
                                    geometry_epoch.clone(),
                                    foreground_epoch.clone(),
                                ) {
                                    Ok(_) => {
                                        attached = Some(AttachedTarget {
                                            hwnd: hwnd_str.to_string(),
                                            hwnd_num,
                                            pid,
                                            create_time: expected_create_time.to_string(),
                                            geometry_epoch,
                                            foreground_epoch,
                                        });
                                        resp.result = json!({
                                            "status": "attached",
                                            "hwnd": hwnd_str,
                                            "pid": pid,
                                            "geometry_epoch": 1,
                                            "foreground_epoch": 1,
                                        });
                                    }
                                    Err(e) => {
                                        resp.ok = false;
                                        resp.error = Some(e);
                                    }
                                }
                            }
                            Err(e) => {
                                resp.ok = false;
                                resp.error = Some(ProtocolError::new("window_gone", e));
                            }
                        }
                    }
                    None => {
                        resp.ok = false;
                        resp.error = Some(ProtocolError::new("invalid_request", "Missing required u32 'pid' for identity binding"));
                    }
                }
            }
            "detach" => {
                capture_worker.stop_capture();
                uia_worker.clear_tokens();
                attached = None;
                last_observation = None;
                resp.result = json!({ "status": "detached" });
            }
            "observe" => {
                if attached.is_none() {
                    resp.ok = false;
                    resp.error = Some(ProtocolError::new("invalid_request", "No capture target attached"));
                } else {
                    let after_frame_id = req.args.get("after_frame_id").and_then(|v| v.as_u64()).unwrap_or(0);
                    let timeout_ms = req.args.get("timeout_ms").and_then(|v| v.as_u64()).unwrap_or(2000);

                    match capture_worker.observe(after_frame_id, timeout_ms) {
                        Ok((meta, payload)) => {
                            let obs_record = input::LastObservation {
                                observation_id: meta["observation_id"].as_u64().unwrap_or(0),
                                frame_id: meta["frame_id"].as_u64().unwrap_or(0),
                                published_instant: std::time::Instant::now(),
                                geometry_epoch: meta["geometry_epoch"].as_u64().unwrap_or(0),
                                foreground_epoch: meta["foreground_epoch"].as_u64().unwrap_or(0),
                                width: meta["width"].as_u64().unwrap_or(0) as u32,
                                height: meta["height"].as_u64().unwrap_or(0) as u32,
                                bounds_x: meta["capture_bounds_physical_px"]["x"].as_i64().unwrap_or(0) as i32,
                                bounds_y: meta["capture_bounds_physical_px"]["y"].as_i64().unwrap_or(0) as i32,
                                bounds_w: meta["capture_bounds_physical_px"]["w"].as_i64().unwrap_or(0) as i32,
                                bounds_h: meta["capture_bounds_physical_px"]["h"].as_i64().unwrap_or(0) as i32,
                            };
                            last_observation = Some(obs_record);

                            resp.payload_len = payload.len() as u32;
                            resp.result = meta;
                            out_payload = payload;
                        }
                        Err(e) => {
                            resp.ok = false;
                            resp.error = Some(e);
                        }
                    }
                }
            }
            "inspect" => {
                if attached.is_none() {
                    resp.ok = false;
                    resp.error = Some(ProtocolError::new("invalid_request", "No target attached"));
                } else {
                    let hwnd_num = attached.as_ref().unwrap().hwnd_num;
                    let max_depth = req.args.get("max_depth").and_then(|v| v.as_u64()).unwrap_or(8) as usize;
                    let max_elements = req.args.get("max_elements").and_then(|v| v.as_u64()).unwrap_or(500) as usize;
                    let timeout_ms = req.args.get("timeout_ms").and_then(|v| v.as_u64()).unwrap_or(2000);

                    match uia_worker.inspect(hwnd_num, max_depth, max_elements, timeout_ms) {
                        Ok((elements, truncated)) => {
                            resp.result = json!({
                                "elements": elements,
                                "truncated": truncated,
                            });
                        }
                        Err(e) => {
                            resp.ok = false;
                            resp.error = Some(e);
                        }
                    }
                }
            }
            "uia_action" => {
                if attached.is_none() {
                    resp.ok = false;
                    resp.error = Some(ProtocolError::new("invalid_request", "No target attached"));
                } else {
                    let token = match req.args.get("token").and_then(|v| v.as_str()) {
                        Some(t) => t.to_string(),
                        None => {
                            resp.ok = false;
                            resp.error = Some(ProtocolError::new("invalid_request", "Missing required string 'token'"));
                            let _ = write_response(&mut writer, &resp, &[]);
                            continue;
                        }
                    };
                    let action = match req.args.get("action").and_then(|v| v.as_str()) {
                        Some(a) => a.to_string(),
                        None => {
                            resp.ok = false;
                            resp.error = Some(ProtocolError::new("invalid_request", "Missing required string 'action'"));
                            let _ = write_response(&mut writer, &resp, &[]);
                            continue;
                        }
                    };
                    let value = req.args.get("value").and_then(|v| v.as_str()).map(|s| s.to_string());

                    match uia_worker.action(token, action, value) {
                        Ok(res) => {
                            resp.result = res;
                        }
                        Err(e) => {
                            resp.ok = false;
                            resp.error = Some(e);
                        }
                    }
                }
            }
            "click" => {
                if attached.is_none() {
                    resp.ok = false;
                    resp.error = Some(ProtocolError::new("invalid_request", "No target attached"));
                } else {
                    let att = attached.as_ref().unwrap();
                    let identity = input::AttachedIdentity {
                        hwnd: att.hwnd.clone(),
                        hwnd_num: att.hwnd_num,
                        pid: att.pid,
                        create_time: att.create_time.clone(),
                        geometry_epoch: att.geometry_epoch.clone(),
                        foreground_epoch: att.foreground_epoch.clone(),
                    };

                    match input::execute_guarded_click(&identity, last_observation.as_ref(), &req.args) {
                        Ok(res) => {
                            resp.result = res;
                        }
                        Err(e) => {
                            resp.ok = false;
                            resp.error = Some(e);
                        }
                    }
                }
            }
            "shutdown" => {
                capture_worker.shutdown();
                uia_worker.shutdown();
                running.store(false, Ordering::SeqCst);
                resp.result = json!({ "status": "shutting_down" });
            }
            unknown => {
                resp.ok = false;
                resp.error = Some(ProtocolError::new("unsupported", format!("Unknown operation '{}'", unknown)));
            }
        }

        if let Err(e) = write_response(&mut writer, &resp, &out_payload) {
            eprintln!("Failed writing response: {}", e);
            break;
        }
    }

    capture_worker.shutdown();
    unsafe {
        CoUninitialize();
    }
}
