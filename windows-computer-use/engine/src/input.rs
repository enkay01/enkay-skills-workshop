use crate::guard::{self, FrameTarget};
use crate::keys::ChordKey;
use crate::protocol::ProtocolError;
use serde_json::json;
use std::sync::atomic::AtomicU64;
use std::sync::Arc;
use std::time::Instant;

use windows::Win32::Foundation::HWND;
use windows::Win32::UI::Input::KeyboardAndMouse::{
    SendInput, INPUT, INPUT_0, INPUT_KEYBOARD, INPUT_MOUSE, KEYBDINPUT, KEYBD_EVENT_FLAGS,
    KEYEVENTF_KEYUP, KEYEVENTF_UNICODE, MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_LEFTDOWN,
    MOUSEEVENTF_LEFTUP, MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP, MOUSEEVENTF_MOVE,
    MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP, MOUSEEVENTF_VIRTUALDESK, MOUSEEVENTF_WHEEL,
    MOUSEINPUT, VIRTUAL_KEY,
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

pub fn get_window_details(hwnd: HWND) -> (String, u32) {
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

// ---------------------------------------------------------------------------
// Uniform pointer target
// ---------------------------------------------------------------------------

/// The one uniform pointer target argument every pointer action accepts.
///
/// Either form is expressed in the pixels of the observation the caller saw. A
/// bare screen coordinate has no representation here, for the same reason the
/// original click did not accept one.
#[derive(Debug, Clone, serde::Deserialize, Default)]
pub struct TargetArg {
    #[serde(default)]
    pub bbox_frame_px: Option<[i32; 4]>,
    #[serde(default)]
    pub point_frame_px: Option<[i32; 2]>,
}

/// Resolve a pointer target, accepting the legacy flat `target_bbox_frame_px`
/// spelling used by the original click so that existing callers are unchanged.
pub fn resolve_target(
    target: Option<&TargetArg>,
    legacy_bbox: Option<[i32; 4]>,
) -> Result<FrameTarget, ProtocolError> {
    if let Some(t) = target {
        match (&t.bbox_frame_px, &t.point_frame_px) {
            (Some(_), Some(_)) => {
                return Err(ProtocolError::new(
                    "invalid_request",
                    "Pointer target accepts either 'bbox_frame_px' or 'point_frame_px', not both",
                ));
            }
            (Some(b), None) => {
                return Ok(FrameTarget::Bbox { x: b[0], y: b[1], w: b[2], h: b[3] });
            }
            (None, Some(p)) => {
                return Ok(FrameTarget::Point { x: p[0], y: p[1] });
            }
            (None, None) => {
                return Err(ProtocolError::new(
                    "invalid_request",
                    "Pointer target requires 'bbox_frame_px' or 'point_frame_px'",
                ));
            }
        }
    }

    match legacy_bbox {
        Some(b) => Ok(FrameTarget::Bbox { x: b[0], y: b[1], w: b[2], h: b[3] }),
        None => Err(ProtocolError::new(
            "invalid_request",
            "Missing pointer target: supply 'target' with 'bbox_frame_px' or 'point_frame_px'",
        )),
    }
}

// ---------------------------------------------------------------------------
// Shared input injection
// ---------------------------------------------------------------------------

/// Inject a sequence of input events.
///
/// This is the single place input reaches the operating system. It checks that
/// every event was accepted; on a partial failure it releases anything still held
/// so a stuck button or modifier does not silently remain down, and reports how
/// many events were actually injected. No action retries after a partial or
/// ambiguous dispatch.
pub fn inject(inputs: &[INPUT], cleanup: &[INPUT]) -> Result<u32, ProtocolError> {
    if inputs.is_empty() {
        return Ok(0);
    }
    let count = unsafe { SendInput(inputs, std::mem::size_of::<INPUT>() as i32) };
    if count as usize != inputs.len() {
        if !cleanup.is_empty() {
            unsafe {
                SendInput(cleanup, std::mem::size_of::<INPUT>() as i32);
            }
        }
        return Err(ProtocolError::new(
            "input_failed",
            format!("SendInput injected only {}/{} events", count, inputs.len()),
        ));
    }
    Ok(count)
}

// ---------------------------------------------------------------------------
// Event builders
// ---------------------------------------------------------------------------

/// Normalize a physical point into the absolute 0..65535 virtual desktop space.
pub fn normalize_to_virtual(
    screen_x: i32,
    screen_y: i32,
    virtual_screen: (i32, i32, i32, i32),
) -> (i32, i32) {
    let (vx, vy, vw, vh) = virtual_screen;
    let norm_x = (((screen_x - vx) as f64 + 0.5) * 65536.0 / vw as f64) as i32;
    let norm_y = (((screen_y - vy) as f64 + 0.5) * 65536.0 / vh as f64) as i32;
    (norm_x, norm_y)
}

pub fn mouse_move_input(screen_x: i32, screen_y: i32, virtual_screen: (i32, i32, i32, i32)) -> INPUT {
    let (norm_x, norm_y) = normalize_to_virtual(screen_x, screen_y, virtual_screen);
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
    }
}

/// A mouse button press or release at an already-normalized absolute position.
pub fn mouse_button_input(norm_x: i32, norm_y: i32, down: bool, button: MouseButton) -> INPUT {
    let flag = match (button, down) {
        (MouseButton::Left, true) => MOUSEEVENTF_LEFTDOWN,
        (MouseButton::Left, false) => MOUSEEVENTF_LEFTUP,
        (MouseButton::Right, true) => MOUSEEVENTF_RIGHTDOWN,
        (MouseButton::Right, false) => MOUSEEVENTF_RIGHTUP,
        (MouseButton::Middle, true) => MOUSEEVENTF_MIDDLEDOWN,
        (MouseButton::Middle, false) => MOUSEEVENTF_MIDDLEUP,
    };
    INPUT {
        r#type: INPUT_MOUSE,
        Anonymous: INPUT_0 {
            mi: MOUSEINPUT {
                dx: norm_x,
                dy: norm_y,
                mouseData: 0,
                dwFlags: flag | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
                time: 0,
                dwExtraInfo: 0,
            },
        },
    }
}

pub fn wheel_input(norm_x: i32, norm_y: i32, notches: i32) -> INPUT {
    INPUT {
        r#type: INPUT_MOUSE,
        Anonymous: INPUT_0 {
            mi: MOUSEINPUT {
                dx: norm_x,
                dy: norm_y,
                mouseData: (notches * 120) as u32,
                dwFlags: MOUSEEVENTF_WHEEL | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
                time: 0,
                dwExtraInfo: 0,
            },
        },
    }
}

pub fn key_input(vk: u16, up: bool) -> INPUT {
    let mut flags = KEYBD_EVENT_FLAGS(0);
    if up {
        flags = flags | KEYEVENTF_KEYUP;
    }
    INPUT {
        r#type: INPUT_KEYBOARD,
        Anonymous: INPUT_0 {
            ki: KEYBDINPUT {
                wVk: VIRTUAL_KEY(vk),
                wScan: 0,
                dwFlags: flags,
                time: 0,
                dwExtraInfo: 0,
            },
        },
    }
}

/// A single UTF-16 code unit delivered as a Unicode key event.
///
/// Sending one code unit at a time is what lets a character outside the Basic
/// Multilingual Plane arrive intact: the engine sends its surrogate pair as two
/// consecutive events and the receiving application reassembles it.
pub fn unicode_key_input(code_unit: u16, up: bool) -> INPUT {
    let mut flags = KEYEVENTF_UNICODE;
    if up {
        flags = flags | KEYEVENTF_KEYUP;
    }
    INPUT {
        r#type: INPUT_KEYBOARD,
        Anonymous: INPUT_0 {
            ki: KEYBDINPUT {
                wVk: VIRTUAL_KEY(0),
                wScan: code_unit,
                dwFlags: flags,
                time: 0,
                dwExtraInfo: 0,
            },
        },
    }
}

pub fn key_input_for(key: ChordKey, up: bool) -> INPUT {
    match key {
        ChordKey::Virtual(vk) => key_input(vk, up),
        ChordKey::Character(unit) => unicode_key_input(unit, up),
    }
}

/// The release events for buttons a partial failure could leave held.
pub fn release_all_buttons_input(virtual_screen: (i32, i32, i32, i32)) -> Vec<INPUT> {
    let (nx, ny) = normalize_to_virtual(0, 0, virtual_screen);
    vec![
        mouse_button_input(nx, ny, false, MouseButton::Left),
        mouse_button_input(nx, ny, false, MouseButton::Right),
        mouse_button_input(nx, ny, false, MouseButton::Middle),
    ]
}

// ---------------------------------------------------------------------------
// Click
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum MouseButton {
    Left,
    Right,
    Middle,
}

fn default_max_age_ms() -> u64 {
    guard::DEFAULT_MAX_AGE_MS
}

#[derive(Debug, Clone, serde::Deserialize)]
pub struct ClickArgs {
    pub observation_id: u64,
    /// Legacy flat bounding box, retained so existing callers are unchanged.
    #[serde(default)]
    pub target_bbox_frame_px: Option<[i32; 4]>,
    /// Uniform pointer target.
    #[serde(default)]
    pub target: Option<TargetArg>,
    #[serde(default = "default_max_age_ms")]
    pub max_age_ms: u64,
    /// Retained with the original default of false.
    #[serde(default)]
    pub dry_run: bool,
    /// Defaults to a single left click, which is the already verified behaviour.
    #[serde(default)]
    pub button: Option<MouseButton>,
    /// 1 for a single click, 2 for a double click.
    #[serde(default)]
    pub click_count: Option<u32>,
}

pub fn execute_guarded_click(
    attached: Option<&AttachedIdentity>,
    last_obs: Option<&LastObservation>,
    args: ClickArgs,
) -> Result<serde_json::Value, ProtocolError> {
    let button = args.button.unwrap_or(MouseButton::Left);
    let click_count = args.click_count.unwrap_or(1);
    if click_count == 0 || click_count > 2 {
        return Err(ProtocolError::new(
            "invalid_request",
            format!("click_count must be 1 or 2, got {}", click_count),
        ));
    }

    let target = resolve_target(args.target.as_ref(), args.target_bbox_frame_px)?;

    // One shared guard, identical for every click variant. Only the injected
    // flags and repetition differ below.
    let plan = guard::plan_pointer_action(
        attached,
        last_obs,
        args.observation_id,
        args.max_age_ms,
        &target,
    )?;

    let hit_hwnd = HWND(plan.hit_hwnd as *mut _);
    let hit_root = if plan.hit_root_hwnd != 0 {
        HWND(plan.hit_root_hwnd as *mut _)
    } else {
        hit_hwnd
    };
    let (target_title, target_pid) = get_window_details(hit_root);

    let mut result = json!({
        "status": if args.dry_run { "dry_run" } else { "clicked" },
        "action": "click",
        "observation_id": plan.observation_id,
        "target_type": plan.target_type,
        "screen_x": plan.screen_x,
        "screen_y": plan.screen_y,
        "hit_window": {
            "hwnd": plan.hit_hwnd.to_string(),
            "root_hwnd": plan.hit_root_hwnd.to_string(),
            "title": target_title,
            "pid": target_pid,
        },
        "age_ms": plan.age_ms,
        "button": format!("{:?}", button).to_lowercase(),
        "click_count": click_count,
    });

    if args.dry_run {
        result["events_injected"] = json!(0);
        return Ok(result);
    }

    let move_ev = mouse_move_input(plan.screen_x, plan.screen_y, plan.virtual_screen);
    let (nx, ny) = normalize_to_virtual(plan.screen_x, plan.screen_y, plan.virtual_screen);

    let mut inputs: Vec<INPUT> = Vec::with_capacity(1 + (click_count as usize) * 2);
    inputs.push(move_ev);
    for _ in 0..click_count {
        inputs.push(mouse_button_input(nx, ny, true, button));
        inputs.push(mouse_button_input(nx, ny, false, button));
    }

    let cleanup = release_all_buttons_input(plan.virtual_screen);
    let count = inject(&inputs, &cleanup)?;
    result["events_injected"] = json!(count);
    Ok(result)
}
