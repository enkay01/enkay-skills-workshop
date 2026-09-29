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
    pub target_type: String,
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

fn get_window_details(hwnd: HWND) -> (String, u32) {
    if hwnd.0.is_null() {
        return ("".into(), 0);
    }
    let mut pid = 0u32;
    unsafe {
        windows::Win32::UI::WindowsAndMessaging::GetWindowThreadProcessId(hwnd, Some(&mut pid));
        let title_len = windows::Win32::UI::WindowsAndMessaging::GetWindowTextLengthW(hwnd);
        let mut title_buf = vec![0u16; (title_len + 1) as usize];
        if title_len > 0 {
            windows::Win32::UI::WindowsAndMessaging::GetWindowTextW(hwnd, &mut title_buf);
        }
        let title = String::from_utf16_lossy(&title_buf[..title_len as usize]);
        (title, pid)
    }
}

use serde::Deserialize;

#[derive(Debug, Clone, Deserialize)]
pub struct ClickArgs {
    pub observation_id: u64,
    pub target_bbox_frame_px: [i32; 4],
    #[serde(default = "default_max_age_ms")]
    pub max_age_ms: u64,
    #[serde(default)]
    pub dry_run: bool,
}

fn default_max_age_ms() -> u64 {
    500
}

pub fn execute_guarded_click(
    attached: Option<&AttachedIdentity>,
    last_obs: Option<&LastObservation>,
    args: ClickArgs,
) -> Result<serde_json::Value, ProtocolError> {
    let req_obs_id = args.observation_id;
    let [bx, by, bw, bh] = args.target_bbox_frame_px;
    let max_age_ms = args.max_age_ms;
    let dry_run = args.dry_run;

    let obs = match last_obs {
        Some(o) => o,
        None => {
            return Err(ProtocolError::new(
                "stale_observation",
                "No observation recorded",
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

    win_utils::check_interactive_desktop()
        .map_err(|e| ProtocolError::new("desktop_inaccessible", e))?;

    let center_frame_x = bx + bw / 2;
    let center_frame_y = by + bh / 2;

    let (screen_x, screen_y) = if obs.target_type == "monitor" {
        (obs.bounds_x + center_frame_x, obs.bounds_y + center_frame_y)
    } else {
        let att = match attached {
            Some(a) => a,
            None => {
                return Err(ProtocolError::new("invalid_request", "No window target attached"));
            }
        };

        win_utils::validate_window_identity(att.hwnd_num, att.pid, &att.create_time)
            .map_err(|e| ProtocolError::new("window_gone", format!("Window identity invalid: {}", e)))?;

        let target_hwnd = HWND(att.hwnd_num as *mut _);

        let fg_hwnd = unsafe { GetForegroundWindow() };
        let fg_root = unsafe { GetAncestor(fg_hwnd, GA_ROOT) };
        if fg_hwnd != target_hwnd && fg_root != target_hwnd {
            att.foreground_epoch.fetch_add(1, Ordering::SeqCst);
            return Err(ProtocolError::new(
                "foreground_changed",
                format!(
                    "Target window is not in the foreground (current foreground is {:?})",
                    fg_hwnd.0
                ),
            ));
        }

        let cur_bounds = win_utils::get_window_extended_frame_bounds(target_hwnd)
            .map_err(|e| ProtocolError::new("geometry_changed", e))?;

        if cur_bounds.x != obs.bounds_x
            || cur_bounds.y != obs.bounds_y
            || cur_bounds.w != obs.bounds_w
            || cur_bounds.h != obs.bounds_h
        {
            att.geometry_epoch.fetch_add(1, Ordering::SeqCst);
            return Err(ProtocolError::new(
                "geometry_changed",
                format!(
                    "Window bounds moved/resized from ({}, {}, {}, {}) to ({}, {}, {}, {})",
                    obs.bounds_x, obs.bounds_y, obs.bounds_w, obs.bounds_h,
                    cur_bounds.x, cur_bounds.y, cur_bounds.w, cur_bounds.h
                ),
            ));
        }

        let sx = cur_bounds.x + center_frame_x;
        let sy = cur_bounds.y + center_frame_y;

        let hwnd_at_pt = unsafe { WindowFromPoint(POINT { x: sx, y: sy }) };
        let root_at_pt = unsafe { GetAncestor(hwnd_at_pt, GA_ROOT) };
        if hwnd_at_pt != target_hwnd && root_at_pt != target_hwnd {
            return Err(ProtocolError::new(
                "target_occluded",
                format!(
                    "Target point ({}, {}) is occluded by another window {:?} (root {:?})",
                    sx, sy, hwnd_at_pt.0, root_at_pt.0
                ),
            ));
        }

        (sx, sy)
    };

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

    if screen_x < vx || screen_x >= vx + vw || screen_y < vy || screen_y >= vy + vh {
        return Err(ProtocolError::new(
            "invalid_coordinates",
            format!("Screen coordinates ({}, {}) outside virtual screen bounds", screen_x, screen_y),
        ));
    }

    let hwnd_at_pt = unsafe { WindowFromPoint(POINT { x: screen_x, y: screen_y }) };
    let root_at_pt = unsafe { GetAncestor(hwnd_at_pt, GA_ROOT) };
    let (target_title, target_pid) = get_window_details(if !root_at_pt.0.is_null() { root_at_pt } else { hwnd_at_pt });

    if dry_run {
        return Ok(json!({
            "status": "dry_run",
            "observation_id": obs.observation_id,
            "target_type": obs.target_type,
            "screen_x": screen_x,
            "screen_y": screen_y,
            "hit_window": {
                "hwnd": (hwnd_at_pt.0 as usize).to_string(),
                "root_hwnd": (root_at_pt.0 as usize).to_string(),
                "title": target_title,
                "pid": target_pid,
            },
            "age_ms": age_ms,
            "events_injected": 0,
        }));
    }

    // Pixel-center normalization across virtual desktop coordinates (0..65535)
    let norm_x = (((screen_x - vx) as f64 + 0.5) * 65536.0 / vw as f64) as i32;
    let norm_y = (((screen_y - vy) as f64 + 0.5) * 65536.0 / vh as f64) as i32;

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
        "target_type": obs.target_type,
        "screen_x": screen_x,
        "screen_y": screen_y,
        "hit_window": {
            "hwnd": (hwnd_at_pt.0 as usize).to_string(),
            "root_hwnd": (root_at_pt.0 as usize).to_string(),
            "title": target_title,
            "pid": target_pid,
        },
        "age_ms": age_ms,
        "events_injected": count,
    }))
}
