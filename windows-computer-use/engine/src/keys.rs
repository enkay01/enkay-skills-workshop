//! Key name vocabulary.
//!
//! Chords are written as a plus-separated prefix of named modifiers followed by a
//! named key, for example `ctrl+shift+s`. Names are stable strings rather than
//! scan codes or virtual-key numbers so that a caller can carry the same decision
//! across machines and keyboard layouts.
//!
//! A name that is not in the vocabulary is refused with a dedicated error code
//! *before* any input event is generated, so a typo cannot half-execute.

use crate::protocol::ProtocolError;
use windows::Win32::UI::Input::KeyboardAndMouse::{MapVirtualKeyW, MAPVK_VK_TO_VSC};

/// The most specific key of a chord.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ChordKey {
    /// Send this virtual key.
    Virtual(u16),
    /// Send this character as a Unicode key event. Used for characters that have
    /// no virtual key on the current layout.
    Character(u16),
}

/// A parsed key chord: zero or more modifiers plus one key.
#[derive(Debug, Clone)]
pub struct KeyChord {
    pub modifiers: Vec<u16>,
    pub key: ChordKey,
}

/// Named modifiers accepted as a chord prefix.
const MODIFIERS: &[(&str, u16)] = &[
    ("ctrl", 0x11),
    ("control", 0x11),
    ("alt", 0x12),
    ("shift", 0x10),
    ("win", 0x5B),
    ("super", 0x5B),
    ("meta", 0x5B),
    ("lwin", 0x5B),
    ("rwin", 0x5C),
];

/// Named non-printable keys accepted as the final key of a chord.
const NAMED_KEYS: &[(&str, u16)] = &[
    ("enter", 0x0D),
    ("return", 0x0D),
    ("tab", 0x09),
    ("esc", 0x1B),
    ("escape", 0x1B),
    ("space", 0x20),
    ("backspace", 0x08),
    ("delete", 0x2E),
    ("del", 0x2E),
    ("insert", 0x2D),
    ("ins", 0x2D),
    ("home", 0x24),
    ("end", 0x23),
    ("pageup", 0x21),
    ("pgup", 0x21),
    ("pagedown", 0x22),
    ("pgdn", 0x22),
    ("up", 0x26),
    ("down", 0x28),
    ("left", 0x25),
    ("right", 0x27),
    ("printscreen", 0x2C),
    ("capslock", 0x14),
    ("numlock", 0x90),
    ("scrolllock", 0x91),
];

fn unknown_key(detail: impl Into<String>) -> ProtocolError {
    ProtocolError::new("unknown_key", detail)
}

/// Parse a chord expression such as `ctrl+shift+s` or `enter`.
///
/// Returns the dedicated `unknown_key` error for any name outside the vocabulary.
pub fn parse_chord(expression: &str) -> Result<KeyChord, ProtocolError> {
    let raw = expression.trim();
    if raw.is_empty() {
        return Err(unknown_key("Key chord is empty"));
    }

    let mut parts: Vec<&str> = raw.split('+').collect();

    // A trailing '+' means a literal plus key rather than an empty segment.
    if parts.len() >= 2 && parts[parts.len() - 1].is_empty() {
        parts.pop();
        parts.push("+");
    }

    let key_name = parts[parts.len() - 1].trim();
    let modifier_names = &parts[..parts.len() - 1];

    let mut modifiers = Vec::new();
    for name in modifier_names {
        let lowered = name.trim().to_ascii_lowercase();
        match MODIFIERS.iter().find(|(n, _)| *n == lowered) {
            Some((_, vk)) => {
                if modifiers.contains(vk) {
                    return Err(unknown_key(format!("Duplicate modifier '{}' in chord", name.trim())));
                }
                modifiers.push(*vk);
            }
            None => {
                return Err(unknown_key(format!(
                    "Unknown key or modifier '{}' in chord '{}'",
                    name.trim(),
                    raw
                )))
            }
        }
    }

    let key = resolve_key(key_name, raw)?;
    Ok(KeyChord { modifiers, key })
}

/// Resolve a single key name to a virtual key or a Unicode character.
fn resolve_key(name: &str, original: &str) -> Result<ChordKey, ProtocolError> {
    if name.is_empty() {
        return Err(unknown_key(format!("Chord '{}' has no key", original)));
    }

    let lowered = name.to_ascii_lowercase();

    if let Some((_, vk)) = NAMED_KEYS.iter().find(|(n, _)| *n == lowered) {
        return Ok(ChordKey::Virtual(*vk));
    }

    if lowered.len() >= 2 && lowered.starts_with('f') {
        if let Ok(n) = lowered[1..].parse::<u16>() {
            if (1..=24).contains(&n) {
                return Ok(ChordKey::Virtual(0x6F + n));
            }
        }
    }

    if lowered == "shift" {
        return Ok(ChordKey::Virtual(0x10));
    }
    if lowered == "ctrl" || lowered == "control" {
        return Ok(ChordKey::Virtual(0x11));
    }
    if lowered == "alt" {
        return Ok(ChordKey::Virtual(0x12));
    }

    // A single character is a printable key. Prefer the layout's virtual key so
    // that shortcuts keep working, and fall back to a Unicode key event for
    // characters the layout cannot express.
    let mut chars = name.chars();
    let ch = match (chars.next(), chars.next()) {
        (Some(c), None) => c,
        _ => {
            return Err(unknown_key(format!(
                "Unknown key '{}' in chord '{}'",
                name, original
            )))
        }
    };

    if ch.is_ascii_graphic() || ch == ' ' {
        if let Some(vk) = virtual_key_for_ascii(ch) {
            return Ok(ChordKey::Virtual(vk));
        }
    }

    let mut buf = [0u16; 2];
    for unit in ch.encode_utf16(&mut buf) {
        return Ok(ChordKey::Character(*unit));
    }
    Err(unknown_key(format!("Key '{}' cannot be encoded", name)))
}

/// Map an ASCII character to a virtual key code.
fn virtual_key_for_ascii(ch: char) -> Option<u16> {
    let upper = ch.to_ascii_uppercase();
    if !upper.is_ascii() {
        return None;
    }
    let code = upper as u8;
    if !code.is_ascii_uppercase() && !(0x20..=0x7E).contains(&code) {
        return None;
    }
    // Uppercase letters live at 0x41..; map down to 0x30.. so shift is implied.
    let vk = if code.is_ascii_uppercase() { code as u16 } else { code as u16 + 0x30 };
    let scancode = unsafe { MapVirtualKeyW(vk as u32, MAPVK_VK_TO_VSC) };
    if scancode == 0 {
        // Fall back to a scan-code-free approximation for the common case.
        return Some(vk);
    }
    Some(vk)
}
