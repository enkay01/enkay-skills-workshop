//! Shared focused-editor capture and readback verification for committed-text paths.
//!
//! Both clipboard paste and `EM_REPLACESEL` insertion let the application insert
//! the text itself, then confirm by reading the document back. The focus and
//! ownership checks, the expected-text computation, and the bounded verify loop
//! are identical; only the dispatch differs.
use crate::protocol::ProtocolError;
use serde_json::json;
use std::thread::sleep;
use std::time::{Duration, Instant};
use windows::core::Interface;
use windows::Win32::Foundation::HWND;
use windows::Win32::System::Com::{CoCreateInstance, CLSCTX_INPROC_SERVER};
use windows::Win32::UI::Accessibility::{
    CUIAutomation, IUIAutomation, IUIAutomationElement, IUIAutomationTextPattern,
    IUIAutomationTextRange, IUIAutomationValuePattern,
    TextPatternRangeEndpoint_End, TextPatternRangeEndpoint_Start,
    UIA_TextPatternId, UIA_ValuePatternId,
};

pub(crate) const MAX_VERIFY_UNITS: i32 = 65536;

pub(crate) fn unavailable(error: impl std::fmt::Display) -> ProtocolError {
    ProtocolError::with_details("text_verification_unavailable", error.to_string(), json!({
        "verification": "unavailable", "events_injected": 0,
    }))
}

fn normalize(value: String) -> String {
    value.replace("\r\n", "\n")
}

fn read_range(range: &IUIAutomationTextRange) -> Result<String, ProtocolError> {
    let value = unsafe { range.GetText(MAX_VERIFY_UNITS + 1) }.map_err(unavailable)?;
    if value.len() > MAX_VERIFY_UNITS as usize {
        return Err(unavailable("Editor text exceeds the 65536 UTF-16 unit verification limit"));
    }
    Ok(normalize(value.to_string()))
}

/// How the document is read back.
enum VerifyVia {
    Text(IUIAutomationTextPattern),
    Value(IUIAutomationValuePattern),
}

pub(crate) struct Editor {
    uia: IUIAutomation,
    element: IUIAutomationElement,
    via: VerifyVia,
    before: String,
    expected: String,
}

impl Editor {
    /// Capture the focused editor owned by `hwnd` and compute the expected document.
    ///
    /// `TextPattern` gives selection-aware expectations. When it is absent and
    /// `allow_value_fallback` is set, whole-value readback through
    /// `ValuePattern` is used instead, which can only verify insertion into an
    /// empty document (or an empty no-op insert): without a selection the caret
    /// position is unknown, so a mid-document expectation cannot be computed.
    pub(crate) fn capture(hwnd: usize, text: &str, allow_value_fallback: bool) -> Result<Self, ProtocolError> {
        let uia: IUIAutomation = unsafe { CoCreateInstance(&CUIAutomation, None, CLSCTX_INPROC_SERVER) }
            .map_err(unavailable)?;
        let element = unsafe { uia.GetFocusedElement() }.map_err(unavailable)?;
        require_owned_element(&uia, &element, HWND(hwnd as *mut _))?;
        if let Ok(pattern) = text_pattern(&element) {
            let (before, expected) = expected_text(&pattern, text)?;
            if !text.is_empty() && before == expected {
                return Err(unavailable("Replacing a selection with identical text cannot confirm insertion"));
            }
            return Ok(Self { uia, element, via: VerifyVia::Text(pattern), before, expected });
        }
        if !allow_value_fallback {
            return Err(unavailable("Focused editor does not expose TextPattern"));
        }
        let pattern: IUIAutomationValuePattern = unsafe { element.GetCurrentPattern(UIA_ValuePatternId) }
            .and_then(|p| p.cast())
            .map_err(|_| unavailable("Focused editor exposes neither TextPattern nor ValuePattern"))?;
        if unsafe { pattern.CurrentIsReadOnly() }.map_err(unavailable)?.as_bool() {
            return Err(ProtocolError::new("read_only_target", "Focused editor is read-only"));
        }
        let before = normalize(unsafe { pattern.CurrentValue() }.map_err(unavailable)?.to_string());
        let expected = if text.is_empty() {
            before.clone()
        } else if before.is_empty() {
            text.replace("\r\n", "\n")
        } else {
            return Err(unavailable("Value-only editors can only verify insertion into an empty document"));
        };
        Ok(Self { uia, element, via: VerifyVia::Value(pattern), before, expected })
    }

    pub(crate) fn verify_via(&self) -> &'static str {
        match self.via {
            VerifyVia::Text(_) => "text_pattern",
            VerifyVia::Value(_) => "value_pattern",
        }
    }

    /// The focused element's own window: the `EM_REPLACESEL` target.
    pub(crate) fn target_hwnd(&self) -> Result<HWND, ProtocolError> {
        let hwnd = unsafe { self.element.CurrentNativeWindowHandle() }.map_err(unavailable)?;
        if hwnd.0.is_null() {
            return Err(unavailable("Focused editor has no window handle for message delivery"));
        }
        Ok(hwnd)
    }

    fn current_text(&self) -> Result<String, ProtocolError> {
        match &self.via {
            VerifyVia::Text(pattern) => {
                let document = unsafe { pattern.DocumentRange() }.map_err(unavailable)?;
                read_range(&document)
            }
            VerifyVia::Value(pattern) => {
                Ok(normalize(unsafe { pattern.CurrentValue() }.map_err(unavailable)?.to_string()))
            }
        }
    }

    fn require_focus(&self) -> Result<(), ProtocolError> {
        let focused = unsafe { self.uia.GetFocusedElement() }.map_err(unavailable)?;
        let same = unsafe { self.uia.CompareElements(&self.element, &focused) }.map_err(unavailable)?;
        if !same.as_bool() {
            return Err(ProtocolError::new("focus_changed", "Focused editor changed during insertion"));
        }
        Ok(())
    }

    pub(crate) fn require_unchanged(&self, text: &str) -> Result<(), ProtocolError> {
        self.require_focus()?;
        match &self.via {
            VerifyVia::Text(pattern) => {
                let (before, expected) = expected_text(pattern, text)?;
                if before != self.before || expected != self.expected {
                    return Err(ProtocolError::new("text_changed", "Editor content or selection changed before insertion"));
                }
                Ok(())
            }
            VerifyVia::Value(_) => {
                if self.current_text()? != self.before {
                    return Err(ProtocolError::new("text_changed", "Editor content changed before insertion"));
                }
                Ok(())
            }
        }
    }

    pub(crate) fn verify(&self) -> Result<(), ProtocolError> {
        let deadline = Instant::now() + Duration::from_secs(2);
        loop {
            self.require_focus()?;
            if self.current_text()? == self.expected {
                return Ok(());
            }
            if Instant::now() >= deadline {
                return Err(ProtocolError::new("text_mismatch", "Editor text did not match the expected insertion result within 2000 ms"));
            }
            sleep(Duration::from_millis(25));
        }
    }
}

fn text_pattern(element: &IUIAutomationElement) -> Result<IUIAutomationTextPattern, ProtocolError> {
    unsafe { element.GetCurrentPattern(UIA_TextPatternId) }
        .and_then(|p| p.cast())
        .map_err(unavailable)
}

fn require_owned_element(uia: &IUIAutomation, element: &IUIAutomationElement, hwnd: HWND) -> Result<(), ProtocolError> {
    let walker = unsafe { uia.RawViewWalker() }.map_err(unavailable)?;
    let mut parent = element.clone();
    for _ in 0..64 {
        if unsafe { parent.CurrentNativeWindowHandle() }.map_err(unavailable)? == hwnd {
            return Ok(());
        }
        parent = unsafe { walker.GetParentElement(&parent) }.map_err(unavailable)?;
    }
    Err(unavailable("Focused editor does not belong to the attached window"))
}

fn expected_text(pattern: &IUIAutomationTextPattern, text: &str) -> Result<(String, String), ProtocolError> {
    unsafe {
        let selection = pattern.GetSelection().map_err(unavailable)?;
        if selection.Length().map_err(unavailable)? != 1 {
            return Err(unavailable("Insertion verification requires one readable selection or caret"));
        }
        let selected = selection.GetElement(0).map_err(unavailable)?;
        let document = pattern.DocumentRange().map_err(unavailable)?;
        let before = read_range(&document)?;
        let prefix = document.Clone().map_err(unavailable)?;
        prefix.MoveEndpointByRange(TextPatternRangeEndpoint_End, &selected, TextPatternRangeEndpoint_Start).map_err(unavailable)?;
        let suffix = document.Clone().map_err(unavailable)?;
        suffix.MoveEndpointByRange(TextPatternRangeEndpoint_Start, &selected, TextPatternRangeEndpoint_End).map_err(unavailable)?;
        let prefix = read_range(&prefix)?;
        let suffix = read_range(&suffix)?;
        if format!("{}{}{}", prefix, read_range(&selected)?, suffix) != before {
            return Err(unavailable("Editor selection and document text are inconsistent"));
        }
        let expected = format!("{}{}{}", prefix, text.replace("\r\n", "\n"), suffix);
        if expected.encode_utf16().count() > MAX_VERIFY_UNITS as usize {
            return Err(unavailable("Expected document exceeds the verification limit"));
        }
        Ok((before, expected))
    }
}
