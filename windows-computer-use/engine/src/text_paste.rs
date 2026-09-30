//! Guarded clipboard paste with full-document verification on the focused editor.
use crate::clipboard::Clipboard;
use crate::editor::Editor;
use crate::guard;
use crate::input::{inject, key_input, AttachedIdentity, LastObservation};
use crate::protocol::ProtocolError;
use serde_json::{json, Value};
use windows::Win32::UI::Input::KeyboardAndMouse::{GetAsyncKeyState, VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN, VK_RWIN};

fn require_released_modifiers() -> Result<(), ProtocolError> {
    for key in [VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN, VK_RWIN] {
        if unsafe { GetAsyncKeyState(key.0 as i32) } < 0 {
            return Err(ProtocolError::new("modifier_held", "Release keyboard modifiers before pasting"));
        }
    }
    Ok(())
}

fn paste_keys() -> Result<u32, ProtocolError> {
    let ctrl = VK_CONTROL.0;
    let v = 0x56;
    inject(&[
        key_input(ctrl, false), key_input(v, false),
        key_input(v, true), key_input(ctrl, true),
    ], &[key_input(v, true), key_input(ctrl, true)])
}

pub fn execute(
    attached: Option<&AttachedIdentity>, last_obs: Option<&LastObservation>,
    observation_id: Option<u64>, max_age_ms: u64, text: &str,
) -> Result<Value, ProtocolError> {
    let plan = guard::plan_keyboard_action(attached, last_obs, observation_id, max_age_ms)?;
    let editor = Editor::capture(plan.hwnd, text, false)?;
    require_released_modifiers()?;
    if text.is_empty() {
        return Ok(json!({"events_injected": 0, "verification": "matched", "clipboard": "unchanged"}));
    }
    let mut clipboard = Clipboard::stage(text)?;
    let ready = (|| {
        editor.require_unchanged(text)?;
        require_released_modifiers()?;
        guard::plan_keyboard_action(attached, last_obs, observation_id, max_age_ms)?;
        if !clipboard.is_current() {
            return Err(ProtocolError::new("clipboard_changed", "Clipboard changed before paste; no input dispatched"));
        }
        Ok(())
    })();
    if let Err(mut error) = ready {
        error.details = json!({"events_injected": 0, "clipboard": clipboard.restore().unwrap_or("restore_failed")});
        return Err(error);
    }
    // After dispatch, never retry or restore until the intended content is read
    // back. A late consumer must not paste the previous clipboard contents.
    let events = paste_keys().map_err(|mut error| {
        error.details = json!({"verification": "unavailable", "clipboard": "left_staged", "dispatch": "partial_or_ambiguous"});
        error
    })?;
    if let Err(mut error) = editor.verify() {
        error.details = json!({
            "events_injected": events,
            "verification": if error.code == "text_mismatch" { "mismatched" } else { "unavailable" },
            "clipboard": "left_staged",
        });
        return Err(error);
    }
    Ok(json!({
        "events_injected": events, "verification": "matched",
        "clipboard": clipboard.restore().unwrap_or("restore_failed"),
    }))
}
