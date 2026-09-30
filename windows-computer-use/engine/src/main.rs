mod actions;
mod capture;
mod clipboard;
mod editor;
mod guard;
mod input;
mod keys;
mod protocol;
mod text_commit;
mod text_paste;
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

/// The action vocabulary this engine supports.
///
/// Reported by the capability check so a client or a model-facing tool can
/// discover the vocabulary rather than hard-coding it.
const SUPPORTED_ACTIONS: &[&str] = &[
    "click",
    "type_text",
    "press_key",
    "scroll",
    "hover",
    "drag",
    "focus_window",
];

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
        "actions": SUPPORTED_ACTIONS,
    })
}

fn find_monitor_target(args: &serde_json::Value, monitors: &[win_utils::MonitorInfo]) -> Option<win_utils::MonitorInfo> {
    if let Some(idx) = args.get("monitor_index").and_then(|v| v.as_u64()) {
        return monitors.iter().find(|m| m.index == idx as u32).cloned();
    }
    if let Some(hmon_str) = args.get("hmonitor").and_then(|v| v.as_str()) {
        return monitors.iter().find(|m| m.hmonitor == hmon_str).cloned();
    }
    if let Some(dev) = args.get("device_name").and_then(|v| v.as_str()) {
        return monitors.iter().find(|m| m.device_name.eq_ignore_ascii_case(dev)).cloned();
    }
    monitors.iter().find(|m| m.is_primary).cloned().or_else(|| monitors.first().cloned())
}

fn resolve_monitor_target(
    args: &serde_json::Value,
    monitors: &[win_utils::MonitorInfo],
) -> Result<(win_utils::MonitorInfo, usize), ProtocolError> {
    if monitors.is_empty() {
        return Err(ProtocolError::new("no_monitors", "No display monitors detected"));
    }
    let target = find_monitor_target(args, monitors)
        .ok_or_else(|| ProtocolError::new("monitor_not_found", "Specified monitor was not found"))?;
    let hmon_num: usize = target.hmonitor.parse()
        .map_err(|_| ProtocolError::new("invalid_monitor_handle", "Failed to parse monitor handle"))?;
    Ok((target, hmon_num))
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
    let mut attached_monitor: Option<win_utils::MonitorInfo> = None;
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
            "list_monitors" => {
                let monitors = win_utils::list_monitors();
                resp.result = json!({ "monitors": monitors });
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
                                        attached_monitor = None;
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
            "attach_monitor" => {
                let monitors = win_utils::list_monitors();
                let (_target_mon, hmon_num) = match resolve_monitor_target(&req.args, &monitors) {
                    Ok(pair) => pair,
                    Err(e) => {
                        resp.ok = false;
                        resp.error = Some(e);
                        let _ = write_response(&mut writer, &resp, &[]);
                        continue;
                    }
                };

                match capture_worker.start_monitor_capture(hmon_num) {
                    Ok(mon_info) => {
                        attached = None;
                        attached_monitor = Some(mon_info.clone());
                        resp.result = json!({
                            "status": "attached",
                            "target_type": "monitor",
                            "monitor_info": mon_info,
                        });
                    }
                    Err(e) => {
                        resp.ok = false;
                        resp.error = Some(e);
                    }
                }
            }
            "observe_monitor" => {
                let monitors = win_utils::list_monitors();
                let (target_mon, hmon_num) = match resolve_monitor_target(&req.args, &monitors) {
                    Ok(pair) => pair,
                    Err(e) => {
                        resp.ok = false;
                        resp.error = Some(e);
                        let _ = write_response(&mut writer, &resp, &[]);
                        continue;
                    }
                };

                let need_start = match &attached_monitor {
                    Some(curr) => curr.hmonitor != target_mon.hmonitor,
                    None => true,
                } || attached.is_some();

                if need_start {
                    if let Err(e) = capture_worker.start_monitor_capture(hmon_num) {
                        resp.ok = false;
                        resp.error = Some(e);
                        let _ = write_response(&mut writer, &resp, &[]);
                        continue;
                    }
                    attached = None;
                    attached_monitor = Some(target_mon.clone());
                }

                let after_frame_id = req.args.get("after_frame_id").and_then(|v| v.as_u64()).unwrap_or(0);
                let timeout_ms = req.args.get("timeout_ms").and_then(|v| v.as_u64()).unwrap_or(2000);

                match capture_worker.observe(after_frame_id, timeout_ms) {
                    Ok((meta, payload)) => {
                        let obs_record = input::LastObservation {
                            observation_id: meta["observation_id"].as_u64().unwrap_or(0),
                            frame_id: meta["frame_id"].as_u64().unwrap_or(0),
                            target_type: meta["target_type"].as_str().unwrap_or("monitor").to_string(),
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
            "detach" => {
                capture_worker.stop_capture();
                uia_worker.clear_tokens();
                attached = None;
                attached_monitor = None;
                last_observation = None;
                resp.result = json!({ "status": "detached" });
            }
            "observe" => {
                let is_monitor_req = req.args.get("target").and_then(|v| v.as_str()) == Some("monitor")
                    || req.args.get("monitor_index").is_some()
                    || req.args.get("hmonitor").is_some()
                    || req.args.get("device_name").is_some();

                if is_monitor_req {
                    let monitors = win_utils::list_monitors();
                    let (target_mon, hmon_num) = match resolve_monitor_target(&req.args, &monitors) {
                        Ok(pair) => pair,
                        Err(e) => {
                            resp.ok = false;
                            resp.error = Some(e);
                            let _ = write_response(&mut writer, &resp, &[]);
                            continue;
                        }
                    };

                    let need_start = match &attached_monitor {
                        Some(curr) => curr.hmonitor != target_mon.hmonitor,
                        None => true,
                    } || attached.is_some();

                    if need_start {
                        if let Err(e) = capture_worker.start_monitor_capture(hmon_num) {
                            resp.ok = false;
                            resp.error = Some(e);
                            let _ = write_response(&mut writer, &resp, &[]);
                            continue;
                        }
                        attached = None;
                        attached_monitor = Some(target_mon.clone());
                    }
                }

                if attached.is_none() && attached_monitor.is_none() {
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
                                target_type: meta["target_type"].as_str().unwrap_or("window").to_string(),
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
                let identity = attached.as_ref().map(|att| input::AttachedIdentity {
                    hwnd: att.hwnd.clone(),
                    hwnd_num: att.hwnd_num,
                    pid: att.pid,
                    create_time: att.create_time.clone(),
                    geometry_epoch: att.geometry_epoch.clone(),
                    foreground_epoch: att.foreground_epoch.clone(),
                });

                let is_monitor_obs = last_observation
                    .as_ref()
                    .map(|o| o.target_type == "monitor")
                    .unwrap_or(false);

                if identity.is_none() && !is_monitor_obs {
                    resp.ok = false;
                    resp.error = Some(ProtocolError::new("invalid_request", "No target attached"));
                } else {
                    let click_args: input::ClickArgs = match serde_json::from_value(req.args.clone()) {
                        Ok(c) => c,
                        Err(e) => {
                            resp.ok = false;
                            resp.error = Some(ProtocolError::new("invalid_request", format!("Invalid click args: {e}")));
                            let _ = write_response(&mut writer, &resp, &[]);
                            continue;
                        }
                    };
                    match input::execute_guarded_click(identity.as_ref(), last_observation.as_ref(), click_args) {
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
            "type_text" | "press_key" | "scroll" | "hover" | "drag" => {
                let identity = attached.as_ref().map(|att| input::AttachedIdentity {
                    hwnd: att.hwnd.clone(),
                    hwnd_num: att.hwnd_num,
                    pid: att.pid,
                    create_time: att.create_time.clone(),
                    geometry_epoch: att.geometry_epoch.clone(),
                    foreground_epoch: att.foreground_epoch.clone(),
                });

                if identity.is_none() {
                    resp.ok = false;
                    resp.error = Some(ProtocolError::new("invalid_request", "No target attached"));
                    let _ = write_response(&mut writer, &resp, &[]);
                    continue;
                }

                let outcome = match req.op.as_str() {
                    "type_text" => serde_json::from_value::<actions::TypeTextArgs>(req.args.clone())
                        .map_err(|e| ProtocolError::new("invalid_request", format!("Invalid type_text args: {e}")))
                        .and_then(|a| actions::execute_type_text(identity.as_ref(), last_observation.as_ref(), a)),
                    "press_key" => serde_json::from_value::<actions::PressKeyArgs>(req.args.clone())
                        .map_err(|e| ProtocolError::new("invalid_request", format!("Invalid press_key args: {e}")))
                        .and_then(|a| actions::execute_press_key(identity.as_ref(), last_observation.as_ref(), a)),
                    "scroll" => serde_json::from_value::<actions::ScrollArgs>(req.args.clone())
                        .map_err(|e| ProtocolError::new("invalid_request", format!("Invalid scroll args: {e}")))
                        .and_then(|a| actions::execute_scroll(identity.as_ref(), last_observation.as_ref(), a)),
                    "hover" => serde_json::from_value::<actions::HoverArgs>(req.args.clone())
                        .map_err(|e| ProtocolError::new("invalid_request", format!("Invalid hover args: {e}")))
                        .and_then(|a| actions::execute_hover(identity.as_ref(), last_observation.as_ref(), a)),
                    "drag" => serde_json::from_value::<actions::DragArgs>(req.args.clone())
                        .map_err(|e| ProtocolError::new("invalid_request", format!("Invalid drag args: {e}")))
                        .and_then(|a| actions::execute_drag(identity.as_ref(), last_observation.as_ref(), a)),
                    _ => unreachable!(),
                };

                match outcome {
                    Ok(res) => {
                        resp.result = res;
                    }
                    Err(e) => {
                        resp.ok = false;
                        resp.error = Some(e);
                    }
                }
            }
            "focus_window" => {
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
                let expected_create_time = req.args.get("process_create_time_utc").and_then(|v| v.as_str());

                match win_utils::focus_window_verified(hwnd_num, expected_pid, expected_create_time) {
                    Ok(outcome) => {
                        // A successful focus change invalidates everything that
                        // belonged to the previous foreground window: bump the
                        // attached target's foreground epoch and drop the stored
                        // observation, so a target chosen before switching cannot
                        // be reused after it.
                        if let Some(att) = attached.as_ref() {
                            att.foreground_epoch.fetch_add(1, Ordering::SeqCst);
                        }
                        last_observation = None;

                        resp.result = json!({
                            "status": "focused",
                            "hwnd": outcome.hwnd,
                            "verified": outcome.verified,
                            "restored_from_minimized": outcome.restored_from_minimized,
                            "requested": outcome.requested,
                            "previous_foreground": outcome.previous_foreground,
                            "foreground": outcome.foreground,
                            "foreground_epoch_bumped": attached.is_some(),
                            "observation_cleared": true,
                        });
                    }
                    Err(probe) => {
                        let details = match probe.actual_foreground {
                            Some(w) => json!({ "actual_foreground": w }),
                            None => serde_json::Value::Null,
                        };
                        resp.ok = false;
                        resp.error = Some(ProtocolError::with_details(
                            probe.code,
                            probe.message,
                            details,
                        ));
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
