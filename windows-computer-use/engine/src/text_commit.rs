//! Committed-text insertion with one `EM_REPLACESEL` message, verified by readback.
//!
//! The focused editor's own window receives a single synchronous edit message,
//! so the application inserts committed text itself: no synthesized keystrokes
//! (and no TSF composition churn downstream of them), no clipboard ownership,
//! no focus race at delivery. `EM_REPLACESEL` replaces the current selection,
//! or inserts at the caret when there is none, and is undoable. Delivery is
//! atomic at the message boundary; the bounded `SendMessageTimeoutW` keeps a
//! hung target from hanging the engine, and readback verification decides
//! success — never the send return value.
use crate::editor::Editor;
use crate::guard;
use crate::input::{AttachedIdentity, LastObservation};
use crate::protocol::ProtocolError;
use serde_json::{json, Value};
use windows::Win32::Foundation::{HWND, LPARAM, WPARAM};
use windows::Win32::UI::WindowsAndMessaging::{SendMessageTimeoutW, SMTO_ABORTIFHUNG};

/// Replaces the selected text, or inserts at the caret with no selection.
/// `wParam` nonzero keeps the replacement on the undo stack.
const EM_REPLACESEL: u32 = 0x00C2;

fn send_replacesel(target: HWND, text: &str) -> isize {
    let wide: Vec<u16> = text.encode_utf16().chain(std::iter::once(0)).collect();
    let mut result: isize = 0;
    unsafe {
        SendMessageTimeoutW(
            target,
            EM_REPLACESEL,
            WPARAM(1),
            LPARAM(wide.as_ptr() as isize),
            SMTO_ABORTIFHUNG,
            2000,
            Some(&mut result as *mut isize as *mut usize),
        )
    };
    result
}

pub fn execute(
    attached: Option<&AttachedIdentity>, last_obs: Option<&LastObservation>,
    observation_id: Option<u64>, max_age_ms: u64, text: &str,
) -> Result<Value, ProtocolError> {
    let plan = guard::plan_keyboard_action(attached, last_obs, observation_id, max_age_ms)?;
    let editor = Editor::capture(plan.hwnd, text, true)?;
    if text.is_empty() {
        return Ok(json!({
            "messages_sent": 0, "events_injected": 0,
            "verification": "matched", "verify_via": editor.verify_via(),
            "clipboard": "unchanged",
        }));
    }
    let target = editor.target_hwnd()?;
    if let Err(error) = (|| {
        editor.require_unchanged(text)?;
        guard::plan_keyboard_action(attached, last_obs, observation_id, max_age_ms)?;
        Ok::<(), ProtocolError>(())
    })() {
        return Err(error);
    }
    // After dispatch, never retry: the message is atomic, and resending would
    // duplicate the text. The send return is diagnostic only; verification
    // reads back what the application actually recorded.
    let send_result = send_replacesel(target, text);
    if let Err(mut error) = editor.verify() {
        error.details = json!({
            "messages_sent": 1,
            "events_injected": 0,
            "send_result": send_result,
            "verification": if error.code == "text_mismatch" { "mismatched" } else { "unavailable" },
            "verify_via": editor.verify_via(),
            "clipboard": "unchanged",
        });
        return Err(error);
    }
    Ok(json!({
        "messages_sent": 1, "events_injected": 0,
        "verification": "matched", "verify_via": editor.verify_via(),
        "clipboard": "unchanged",
    }))
}
