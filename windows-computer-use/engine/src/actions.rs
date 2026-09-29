//! Desktop actions beyond click.
//!
//! Each action is a composition of the shared checks in [`crate::guard`] plus its
//! own injection sequence. None of them re-implements the safety preamble, and
//! none of them retries after a partial or ambiguous dispatch.

use crate::guard::{self, FrameTarget};
use crate::input::{
    self, inject, key_input, key_input_for, mouse_button_input, mouse_move_input,
    normalize_to_virtual, release_all_buttons_input, unicode_key_input, wheel_input,
    AttachedIdentity, LastObservation, MouseButton, TargetArg,
};
use crate::keys::parse_chord;
use crate::protocol::ProtocolError;
use serde::Deserialize;
use serde_json::json;
use std::thread::sleep;
use std::time::Duration;

/// Longest string accepted by `type_text`, so a runaway request fails cleanly
/// rather than injecting an unbounded sequence.
pub const MAX_TYPE_CHARS: usize = 4096;
/// Most wheel notches accepted per axis per call.
pub const MAX_SCROLL_NOTCHES: i32 = 100;
/// Most interpolated moves in one drag.
pub const MAX_DRAG_STEPS: u32 = 120;
/// Most repetitions of one chord in a single call.
pub const MAX_KEY_REPEATS: u32 = 10;

fn default_max_age_ms() -> u64 {
    guard::DEFAULT_MAX_AGE_MS
}

fn default_false() -> bool {
    false
}

// ---------------------------------------------------------------------------
// Typing
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Deserialize)]
pub struct TypeTextArgs {
    pub text: String,
    #[serde(default)]
    pub observation_id: Option<u64>,
    #[serde(default = "default_max_age_ms")]
    pub max_age_ms: u64,
    /// Optional per-character delay. A small number of applications drop
    /// characters from a single unthrottled batch.
    #[serde(default)]
    pub delay_ms: Option<u64>,
    #[serde(default = "default_false")]
    pub dry_run: bool,
}

pub fn execute_type_text(
    attached: Option<&AttachedIdentity>,
    last_obs: Option<&LastObservation>,
    args: TypeTextArgs,
) -> Result<serde_json::Value, ProtocolError> {
    let char_count = args.text.chars().count();
    if char_count > MAX_TYPE_CHARS {
        return Err(ProtocolError::new(
            "invalid_request",
            format!(
                "Text length {} characters exceeds the limit of {}",
                char_count, MAX_TYPE_CHARS
            ),
        ));
    }

    let plan = guard::plan_keyboard_action(
        attached,
        last_obs,
        args.observation_id,
        args.max_age_ms,
    )?;

    // One UTF-16 code unit at a time, with a matching key-up per key-down, so no
    // character is dropped or substituted and non-BMP text arrives as its
    // surrogate pair.
    let code_units: Vec<u16> = args.text.encode_utf16().collect();

    let mut result = json!({
        "status": if args.dry_run { "dry_run" } else { "typed" },
        "action": "type_text",
        "characters_sent": char_count,
        "code_units_sent": code_units.len(),
        "observation_id": plan.observation_id,
        "age_ms": plan.age_ms,
        "target_hwnd": plan.hwnd.to_string(),
        "target_pid": plan.pid,
    });

    if args.dry_run {
        result["events_injected"] = json!(0);
        return Ok(result);
    }

    let mut events_injected = 0u32;
    let delay = Duration::from_millis(args.delay_ms.unwrap_or(0));

    for unit in &code_units {
        let pair = [unicode_key_input(*unit, false), unicode_key_input(*unit, true)];
        events_injected += inject(&pair, &[])?;
        if !delay.is_zero() {
            sleep(delay);
        }
    }

    result["events_injected"] = json!(events_injected);
    Ok(result)
}

// ---------------------------------------------------------------------------
// Key chords
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Deserialize)]
pub struct PressKeyArgs {
    /// Plus-separated modifier prefix followed by a named key, e.g. `ctrl+shift+s`.
    pub chord: String,
    #[serde(default)]
    pub observation_id: Option<u64>,
    #[serde(default = "default_max_age_ms")]
    pub max_age_ms: u64,
    /// Bounded number of repetitions.
    #[serde(default)]
    pub repeat: Option<u32>,
    /// How long the keys are held, in milliseconds.
    #[serde(default)]
    pub hold_ms: Option<u64>,
    #[serde(default = "default_false")]
    pub dry_run: bool,
}

pub fn execute_press_key(
    attached: Option<&AttachedIdentity>,
    last_obs: Option<&LastObservation>,
    args: PressKeyArgs,
) -> Result<serde_json::Value, ProtocolError> {
    // Parsed before anything else so an unrecognised name can never half-execute.
    let parsed = parse_chord(&args.chord)?;

    let repeat = args.repeat.unwrap_or(1);
    if repeat == 0 || repeat > MAX_KEY_REPEATS {
        return Err(ProtocolError::new(
            "invalid_request",
            format!("repeat must be between 1 and {}, got {}", MAX_KEY_REPEATS, repeat),
        ));
    }

    let plan = guard::plan_keyboard_action(
        attached,
        last_obs,
        args.observation_id,
        args.max_age_ms,
    )?;

    let hold = Duration::from_millis(args.hold_ms.unwrap_or(0));

    let mut result = json!({
        "status": if args.dry_run { "dry_run" } else { "key_sent" },
        "action": "press_key",
        "chord": args.chord,
        "modifiers": parsed.modifiers.len(),
        "repeat": repeat,
        "observation_id": plan.observation_id,
        "age_ms": plan.age_ms,
        "target_hwnd": plan.hwnd.to_string(),
        "target_pid": plan.pid,
    });

    if args.dry_run {
        result["events_injected"] = json!(0);
        return Ok(result);
    }

    let mut events_injected = 0u32;
    for _ in 0..repeat {
        let mut events: Vec<windows::Win32::UI::Input::KeyboardAndMouse::INPUT> = Vec::new();
        for vk in &parsed.modifiers {
            events.push(key_input(*vk, false));
        }
        events.push(key_input_for(parsed.key, false));
        if !hold.is_zero() {
            sleep(hold);
        }
        for vk in parsed.modifiers.iter().rev() {
            events.push(key_input(*vk, true));
        }
        events.push(key_input_for(parsed.key, true));

        let cleanup = modifier_cleanup(&parsed);
        events_injected += inject(&events, &cleanup)?;
    }

    result["events_injected"] = json!(events_injected);
    Ok(result)
}

/// Key-up events for the modifiers a partial failure could leave held.
fn modifier_cleanup(
    chord: &crate::keys::KeyChord,
) -> Vec<windows::Win32::UI::Input::KeyboardAndMouse::INPUT> {
    let mut cleanup: Vec<windows::Win32::UI::Input::KeyboardAndMouse::INPUT> =
        chord.modifiers.iter().rev().map(|vk| key_input(*vk, true)).collect();
    cleanup.push(key_input_for(chord.key, true));
    cleanup
}

// ---------------------------------------------------------------------------
// Scrolling
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Deserialize)]
pub struct ScrollArgs {
    #[serde(default)]
    pub notches_x: Option<i32>,
    #[serde(default)]
    pub notches_y: Option<i32>,
    /// Optional. When absent the scroll is sent at the current pointer position
    /// and the hit-test ownership check does not apply.
    #[serde(default)]
    pub target: Option<TargetArg>,
    #[serde(default)]
    pub target_bbox_frame_px: Option<[i32; 4]>,
    pub observation_id: u64,
    #[serde(default = "default_max_age_ms")]
    pub max_age_ms: u64,
    #[serde(default = "default_false")]
    pub dry_run: bool,
}

pub fn execute_scroll(
    attached: Option<&AttachedIdentity>,
    last_obs: Option<&LastObservation>,
    args: ScrollArgs,
) -> Result<serde_json::Value, ProtocolError> {
    let requested_x = args.notches_x.unwrap_or(0);
    let requested_y = args.notches_y.unwrap_or(0);
    if requested_x == 0 && requested_y == 0 {
        return Err(ProtocolError::new(
            "invalid_request",
            "Scroll requires a non-zero 'notches_x' or 'notches_y'",
        ));
    }

    let notches_x = requested_x.clamp(-MAX_SCROLL_NOTCHES, MAX_SCROLL_NOTCHES);
    let notches_y = requested_y.clamp(-MAX_SCROLL_NOTCHES, MAX_SCROLL_NOTCHES);
    let clamped = notches_x != requested_x || notches_y != requested_y;

    // A scroll without a target is allowed, and is reported as unchecked.
    let target = if args.target.is_some() || args.target_bbox_frame_px.is_some() {
        Some(input::resolve_target(args.target.as_ref(), args.target_bbox_frame_px)?)
    } else {
        None
    };

    let plan = guard::plan_pointer_action(
        attached,
        last_obs,
        args.observation_id,
        args.max_age_ms,
        target.as_ref().unwrap_or(&FrameTarget::Point { x: 0, y: 0 }),
    )?;

    // Without an explicit target the wheel is sent where the pointer already is,
    // so no point is claimed and the hit-test ownership check does not apply.
    let hit_test_applied = plan.hit_test_applied && target.is_some();

    let mut result = json!({
        "status": if args.dry_run { "dry_run" } else { "scrolled" },
        "action": "scroll",
        "observation_id": plan.observation_id,
        "target_type": plan.target_type,
        "screen_x": plan.screen_x,
        "screen_y": plan.screen_y,
        "age_ms": plan.age_ms,
        "notches_x": notches_x,
        "notches_y": notches_y,
        "explicit_target": target.is_some(),
        "hit_test_applied": hit_test_applied,
        "limit": MAX_SCROLL_NOTCHES,
        "clamped": clamped,
    });

    if args.dry_run {
        result["events_injected"] = json!(0);
        return Ok(result);
    }

    let (nx, ny) = normalize_to_virtual(plan.screen_x, plan.screen_y, plan.virtual_screen);

    let mut events: Vec<windows::Win32::UI::Input::KeyboardAndMouse::INPUT> = Vec::new();

    // A wheel event acts on wherever the pointer currently is and ignores the
    // coordinates it carries, so a scroll aimed at a target has to put the
    // pointer there first. Without a target the pointer is left alone.
    if target.is_some() {
        events.push(mouse_move_input(plan.screen_x, plan.screen_y, plan.virtual_screen));
    }

    // Per-notch events, so intermediate scroll messages reach the application.
    for _ in 0..notches_y.abs() {
        events.push(wheel_input(nx, ny, notches_y.signum()));
    }
    for _ in 0..notches_x.abs() {
        events.push(horizontal_wheel_input(nx, ny, notches_x.signum()));
    }

    let count = inject(&events, &[])?;
    result["pointer_moved"] = json!(target.is_some());
    result["events_injected"] = json!(count);
    Ok(result)
}

// ---------------------------------------------------------------------------
// Hover
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Deserialize)]
pub struct HoverArgs {
    pub observation_id: u64,
    #[serde(default)]
    pub target: Option<TargetArg>,
    #[serde(default)]
    pub target_bbox_frame_px: Option<[i32; 4]>,
    #[serde(default = "default_max_age_ms")]
    pub max_age_ms: u64,
    /// Interpolate the movement over this duration. Without it the pointer is
    /// moved in a single jump, which is the more reliable default.
    #[serde(default)]
    pub duration_ms: Option<u64>,
    #[serde(default = "default_false")]
    pub dry_run: bool,
}

pub fn execute_hover(
    attached: Option<&AttachedIdentity>,
    last_obs: Option<&LastObservation>,
    args: HoverArgs,
) -> Result<serde_json::Value, ProtocolError> {
    let target = input::resolve_target(args.target.as_ref(), args.target_bbox_frame_px)?;
    let plan = guard::plan_pointer_action(
        attached,
        last_obs,
        args.observation_id,
        args.max_age_ms,
        &target,
    )?;

    let mut result = json!({
        "status": if args.dry_run { "dry_run" } else { "hovered" },
        "action": "hover",
        "observation_id": plan.observation_id,
        "target_type": plan.target_type,
        "screen_x": plan.screen_x,
        "screen_y": plan.screen_y,
        "age_ms": plan.age_ms,
        "hit_test_applied": plan.hit_test_applied,
        "moves": 1,
    });

    if args.dry_run {
        result["events_injected"] = json!(0);
        return Ok(result);
    }

    let start = current_pointer_position();
    let duration = Duration::from_millis(args.duration_ms.unwrap_or(0));
    let steps = if duration.is_zero() { 1 } else { interpolate_steps(duration) };

    let count = move_pointer(
        start,
        (plan.screen_x, plan.screen_y),
        steps,
        duration,
        plan.virtual_screen,
    )?;

    result["moves"] = json!(steps);
    result["events_injected"] = json!(count);
    Ok(result)
}

// ---------------------------------------------------------------------------
// Drag
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Deserialize)]
pub struct DragArgs {
    pub observation_id: u64,
    /// Start point, in the same observed-frame coordinate system as the end point.
    #[serde(default)]
    pub from: Option<TargetArg>,
    #[serde(default)]
    pub to: Option<TargetArg>,
    #[serde(default = "default_max_age_ms")]
    pub max_age_ms: u64,
    /// Number of interpolated intermediate moves.
    #[serde(default)]
    pub steps: Option<u32>,
    #[serde(default)]
    pub duration_ms: Option<u64>,
    #[serde(default = "default_false")]
    pub dry_run: bool,
}

pub fn execute_drag(
    attached: Option<&AttachedIdentity>,
    last_obs: Option<&LastObservation>,
    args: DragArgs,
) -> Result<serde_json::Value, ProtocolError> {
    let from = input::resolve_target(args.from.as_ref(), None)?;
    let to = input::resolve_target(args.to.as_ref(), None)?;

    let steps = args.steps.unwrap_or(20);
    if steps == 0 || steps > MAX_DRAG_STEPS {
        return Err(ProtocolError::new(
            "invalid_request",
            format!("steps must be between 1 and {}, got {}", MAX_DRAG_STEPS, steps),
        ));
    }

    // Both endpoints run the full shared guard, so both are bound to the same
    // observation and the same live window.
    let from_plan = guard::plan_pointer_action(
        attached,
        last_obs,
        args.observation_id,
        args.max_age_ms,
        &from,
    )?;
    let to_plan = guard::plan_pointer_action(
        attached,
        last_obs,
        args.observation_id,
        args.max_age_ms,
        &to,
    )?;

    let mut result = json!({
        "status": if args.dry_run { "dry_run" } else { "dragged" },
        "action": "drag",
        "observation_id": from_plan.observation_id,
        "target_type": from_plan.target_type,
        "from": { "screen_x": from_plan.screen_x, "screen_y": from_plan.screen_y },
        "to": { "screen_x": to_plan.screen_x, "screen_y": to_plan.screen_y },
        "age_ms": from_plan.age_ms,
        "steps": steps,
        "limit": MAX_DRAG_STEPS,
    });

    if args.dry_run {
        result["events_injected"] = json!(0);
        return Ok(result);
    }

    let vs = from_plan.virtual_screen;
    let (fnx, fny) = normalize_to_virtual(from_plan.screen_x, from_plan.screen_y, vs);
    let (tnx, tny) = normalize_to_virtual(to_plan.screen_x, to_plan.screen_y, vs);

    let duration = Duration::from_millis(args.duration_ms.unwrap_or(0));
    let per_step = if duration.is_zero() {
        Duration::ZERO
    } else {
        duration / steps
    };

    // Press at the start, interpolate, release at the end. Intermediate points
    // are interpolated in physical screen space and are deliberately not
    // re-hit-tested: during a legitimate drag the pointer is expected to travel
    // over content that is not the press target.
    let mut events: Vec<windows::Win32::UI::Input::KeyboardAndMouse::INPUT> = Vec::new();
    events.push(mouse_move_input(from_plan.screen_x, from_plan.screen_y, vs));
    events.push(mouse_button_input(fnx, fny, true, MouseButton::Left));

    for i in 1..=steps {
        let t = i as f64 / steps as f64;
        let ix = from_plan.screen_x as f64 + (to_plan.screen_x - from_plan.screen_x) as f64 * t;
        let iy = from_plan.screen_y as f64 + (to_plan.screen_y - from_plan.screen_y) as f64 * t;
        let (inx, iny) = (ix.round() as i32, iy.round() as i32);
        events.push(mouse_move_input(inx, iny, vs));
        if !per_step.is_zero() {
            sleep(per_step);
        }
    }

    events.push(mouse_button_input(tnx, tny, false, MouseButton::Left));

    let cleanup = release_all_buttons_input(vs);
    let count = inject(&events, &cleanup)?;

    result["events_injected"] = json!(count);
    Ok(result)
}

// ---------------------------------------------------------------------------
// Shared movement helpers
// ---------------------------------------------------------------------------

/// Move the pointer to a destination, optionally interpolating so that
/// applications tracking motion receive intermediate moves.
fn move_pointer(
    from: (i32, i32),
    to: (i32, i32),
    steps: u32,
    duration: Duration,
    virtual_screen: (i32, i32, i32, i32),
) -> Result<u32, ProtocolError> {
    if steps <= 1 {
        let ev = mouse_move_input(to.0, to.1, virtual_screen);
        return inject(&[ev], &[]);
    }

    let per_step = duration / steps;
    let mut count = 0u32;
    for i in 1..=steps {
        let t = i as f64 / steps as f64;
        let ix = from.0 as f64 + (to.0 - from.0) as f64 * t;
        let iy = from.1 as f64 + (to.1 - from.1) as f64 * t;
        let ev = mouse_move_input(ix.round() as i32, iy.round() as i32, virtual_screen);
        count += inject(&[ev], &[])?;
        if !per_step.is_zero() {
            sleep(per_step);
        }
    }
    Ok(count)
}

fn interpolate_steps(duration: Duration) -> u32 {
    // Roughly one move per 8ms, bounded.
    let ms = duration.as_millis() as u32;
    (ms / 8).clamp(2, 120)
}

fn current_pointer_position() -> (i32, i32) {
    unsafe {
        let mut pt = windows::Win32::Foundation::POINT { x: 0, y: 0 };
        let _ = windows::Win32::UI::WindowsAndMessaging::GetCursorPos(
            &mut pt,
        );
        (pt.x, pt.y)
    }
}

fn horizontal_wheel_input(
    norm_x: i32,
    norm_y: i32,
    notches: i32,
) -> windows::Win32::UI::Input::KeyboardAndMouse::INPUT {
    use windows::Win32::UI::Input::KeyboardAndMouse::{
        INPUT, INPUT_0, INPUT_MOUSE, MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_HWHEEL,
        MOUSEEVENTF_VIRTUALDESK, MOUSEINPUT,
    };
    INPUT {
        r#type: INPUT_MOUSE,
        Anonymous: INPUT_0 {
            mi: MOUSEINPUT {
                dx: norm_x,
                dy: norm_y,
                mouseData: (notches * 120) as u32,
                dwFlags: MOUSEEVENTF_HWHEEL | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
                time: 0,
                dwExtraInfo: 0,
            },
        },
    }
}
