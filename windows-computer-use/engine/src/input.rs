use crate::protocol::ProtocolError;
use crate::win_utils;
use serde_json::json;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Instant;

use windows::Win32::Foundation::{HWND, POINT};
use windows::Win32::UI::Input::KeyboardAndMouse::{
    SendInput, INPUT, INPUT_0, INPUT_MOUSE, MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_LEFTDOWN,
    MOUSEEVENTF_LEFTUP, MOUSEEVENTF_MOVE, MOUSEEVENTF_VIRTUALDESK, MOUSEINPUT,
};
use windows::Win32::UI::WindowsAndMessaging::{
    GetAncestor, GetForegroundWindow, GetSystemMetrics, WindowFromPoint, GA_ROOT,
    SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN, SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN,
};

#[allow(dead_code)]
#[derive(Clone, Debug)]
pub struct LastObservation {
    pub observation_id: u64,
    pub frame_id: u64,
    pub published_instant: Instant,
    pub geometry_epoch: u64,
    pub foreground_epoch: u64,
    pub width: u32,
    pub height: u32,
    pub bounds_x: i32,
    pub bounds_y: i32,
    pub bounds_w: i32,
    pub bounds_h: i32,
}

#[allow(dead_code)]
pub struct AttachedIdentity {
    pub hwnd: String,
    pub hwnd_num: usize,
    pub pid: u32,
    pub create_time: String,
    pub geometry_epoch: Arc<AtomicU64>,
    pub foreground_epoch: Arc<AtomicU64>,
}

pub fn execute_guarded_click(
    attached: &AttachedIdentity,
    last_obs: Option<&LastObservation>,
    args: &serde_json::Value,
) -> Result<serde_json::Value, ProtocolError> {
    // 1. Parse arguments
    let req_obs_id = match args.get("observation_id").and_then(|v| v.as_u64()) {
        Some(id) => id,
        None => {
            return Err(ProtocolError::new(
                "invalid_request",
                "Missing required u64 'observation_id'",
            ));
        }
    };

    let bbox_arr = match args.get("target_bbox_frame_px").and_then(|v| v.as_array()) {
        Some(a) if a.len() == 4 => a,
        _ => {
            return Err(ProtocolError::new(
                "invalid_request",
                "Missing required 4-element array 'target_bbox_frame_px' [x, y, w, h]",
            ));
        }
    };

    let bx = bbox_arr[0].as_i64().unwrap_or(-1) as i32;
    let by = bbox_arr[1].as_i64().unwrap_or(-1) as i32;
    let bw = bbox_arr[2].as_i64().unwrap_or(-1) as i32;
    let bh = bbox_arr[3].as_i64().unwrap_or(-1) as i32;

    let max_age_ms = args.get("max_age_ms").and_then(|v| v.as_u64()).unwrap_or(500);

    // 2. Validate window identity (PID + creation time + alive)
    win_utils::validate_window_identity(attached.hwnd_num, attached.pid, &attached.create_time)
        .map_err(|e| ProtocolError::new("window_gone", format!("Window identity invalid: {}", e)))?;

    let target_hwnd = HWND(attached.hwnd_num as *mut _);

    // 3. Validate observation existence & ID match
    let obs = match last_obs {
        Some(o) => o,
        None => {
            return Err(ProtocolError::new(
                "stale_observation",
                "No observation recorded for target window",
            ));
        }
    };

    if obs.observation_id != req_obs_id {
        return Err(ProtocolError::new(
            "stale_observation",
            format!(
                "Observation id mismatch: requested {}, current latest is {}",
                req_obs_id, obs.observation_id
            ),
        ));
    }

    // 4. Validate observation age
    let age_ms = obs.published_instant.elapsed().as_millis() as u64;
    if age_ms > max_age_ms {
        return Err(ProtocolError::new(
            "stale_observation",
            format!(
                "Observation expired: age {} ms exceeds max allowed {} ms",
                age_ms, max_age_ms
            ),
        ));
    }

    // 5. Validate bounding box coordinates within captured frame
    if bx < 0 || by < 0 || bw <= 0 || bh <= 0
        || (bx + bw) as u32 > obs.width
        || (by + bh) as u32 > obs.height
    {
        return Err(ProtocolError::new(
            "invalid_coordinates",
            format!(
                "Target bbox [{}, {}, {}, {}] is outside frame dimensions [{}x{}]",
                bx, by, bw, bh, obs.width, obs.height
            ),
        ));
    }

    // 6. Check interactive desktop session
    win_utils::check_interactive_desktop()
        .map_err(|e| ProtocolError::new("desktop_inaccessible", e))?;

    // 7. Verify foreground window
    let fg_hwnd = unsafe { GetForegroundWindow() };
    let fg_root = unsafe { GetAncestor(fg_hwnd, GA_ROOT) };
    if fg_hwnd != target_hwnd && fg_root != target_hwnd {
        attached.foreground_epoch.fetch_add(1, Ordering::SeqCst);
        return Err(ProtocolError::new(
            "foreground_changed",
            format!(
                "Target window is not in the foreground (current foreground is {:?})",
                fg_hwnd.0
            ),
        ));
    }

    // 8. Verify geometry (bounds match observation)
    let cur_bounds = win_utils::get_window_extended_frame_bounds(target_hwnd)
        .map_err(|e| ProtocolError::new("geometry_changed", e))?;

    if cur_bounds.x != obs.bounds_x
        || cur_bounds.y != obs.bounds_y
        || cur_bounds.w != obs.bounds_w
        || cur_bounds.h != obs.bounds_h
    {
        attached.geometry_epoch.fetch_add(1, Ordering::SeqCst);
        return Err(ProtocolError::new(
            "geometry_changed",
            format!(
                "Window bounds moved/resized from ({}, {}, {}, {}) to ({}, {}, {}, {})",
                obs.bounds_x, obs.bounds_y, obs.bounds_w, obs.bounds_h,
                cur_bounds.x, cur_bounds.y, cur_bounds.w, cur_bounds.h
            ),
        ));
    }

    // 9. Target point hit-test ownership
    let center_frame_x = bx + bw / 2;
    let center_frame_y = by + bh / 2;
    let screen_x = cur_bounds.x + center_frame_x;
    let screen_y = cur_bounds.y + center_frame_y;

    let hwnd_at_pt = unsafe { WindowFromPoint(POINT { x: screen_x, y: screen_y }) };
    let root_at_pt = unsafe { GetAncestor(hwnd_at_pt, GA_ROOT) };
    if hwnd_at_pt != target_hwnd && root_at_pt != target_hwnd {
        return Err(ProtocolError::new(
            "target_occluded",
            format!(
                "Target point ({}, {}) is occluded by another window {:?} (root {:?})",
                screen_x, screen_y, hwnd_at_pt.0, root_at_pt.0
            ),
        ));
    }

    // 10. Map screen physical coordinates to entire virtual desktop
    let vx = unsafe { GetSystemMetrics(SM_XVIRTUALSCREEN) };
    let vy = unsafe { GetSystemMetrics(SM_YVIRTUALSCREEN) };
    let vw = unsafe { GetSystemMetrics(SM_CXVIRTUALSCREEN) };
    let vh = unsafe { GetSystemMetrics(SM_CYVIRTUALSCREEN) };

    if vw <= 0 || vh <= 0 {
        return Err(ProtocolError::new(
            "input_failed",
            "Virtual screen dimensions are invalid",
        ));
    }

    // Pixel-center normalization across virtual desktop coordinates (0..65535)
    let norm_x = (((screen_x - vx) as f64 + 0.5) * 65536.0 / vw as f64) as i32;
    let norm_y = (((screen_y - vy) as f64 + 0.5) * 65536.0 / vh as f64) as i32;

    // 11. Batch move + down + up injection via SendInput
    let inputs = [
        INPUT {
            r#type: INPUT_MOUSE,
            Anonymous: INPUT_0 {
                mi: MOUSEINPUT {
                    dx: norm_x,
                    dy: norm_y,
                    mouseData: 0,
                    dwFlags: MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
                    time: 0,
                    dwExtraInfo: 0,
                },
            },
        },
        INPUT {
            r#type: INPUT_MOUSE,
            Anonymous: INPUT_0 {
                mi: MOUSEINPUT {
                    dx: norm_x,
                    dy: norm_y,
                    mouseData: 0,
                    dwFlags: MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
                    time: 0,
                    dwExtraInfo: 0,
                },
            },
        },
        INPUT {
            r#type: INPUT_MOUSE,
            Anonymous: INPUT_0 {
                mi: MOUSEINPUT {
                    dx: norm_x,
                    dy: norm_y,
                    mouseData: 0,
                    dwFlags: MOUSEEVENTF_LEFTUP | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
                    time: 0,
                    dwExtraInfo: 0,
                },
            },
        },
    ];

    let count = unsafe { SendInput(&inputs, std::mem::size_of::<INPUT>() as i32) };
    if count != 3 {
        // Best effort release mouse button if down succeeded
        let release = [INPUT {
            r#type: INPUT_MOUSE,
            Anonymous: INPUT_0 {
                mi: MOUSEINPUT {
                    dx: norm_x,
                    dy: norm_y,
                    mouseData: 0,
                    dwFlags: MOUSEEVENTF_LEFTUP | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
                    time: 0,
                    dwExtraInfo: 0,
                },
            },
        }];
        unsafe {
            SendInput(&release, std::mem::size_of::<INPUT>() as i32);
        }
        return Err(ProtocolError::new(
            "input_failed",
            format!("SendInput injected only {}/3 events", count),
        ));
    }

    Ok(json!({
        "status": "clicked",
        "observation_id": obs.observation_id,
        "screen_x": screen_x,
        "screen_y": screen_y,
        "age_ms": age_ms,
        "events_injected": count,
    }))
}
