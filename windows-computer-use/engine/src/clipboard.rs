//! Temporary Unicode clipboard contents, with conservative format preservation.
use crate::protocol::ProtocolError;
use std::thread::sleep;
use std::time::{Duration, Instant};
use windows::core::w;
use windows::Win32::Foundation::{
    GetLastError, GlobalFree, SetLastError, HANDLE, HGLOBAL, HWND, WIN32_ERROR,
};
use windows::Win32::System::DataExchange::{
    CloseClipboard, EmptyClipboard, EnumClipboardFormats, GetClipboardData,
    GetClipboardOwner, GetClipboardSequenceNumber, OpenClipboard, SetClipboardData,
};
use windows::Win32::System::Memory::{GlobalAlloc, GlobalLock, GlobalSize, GlobalUnlock, GMEM_MOVEABLE};
use windows::Win32::UI::WindowsAndMessaging::{
    CreateWindowExW, DestroyWindow, HWND_MESSAGE, WINDOW_EX_STYLE, WINDOW_STYLE,
};

const CF_UNICODETEXT: u32 = 13;
const MAX_CLIPBOARD_BYTES: usize = 16 * 1024 * 1024;

fn clipboard_error(error: impl std::fmt::Display) -> ProtocolError {
    ProtocolError::new("clipboard_failed", error.to_string())
}

struct Owner(HWND);

impl Owner {
    fn new() -> Result<Self, ProtocolError> {
        let hwnd = unsafe {
            CreateWindowExW(
                WINDOW_EX_STYLE(0), w!("STATIC"), w!("wcu clipboard"), WINDOW_STYLE(0),
                0, 0, 0, 0, HWND_MESSAGE, None, None, None,
            )
        }.map_err(clipboard_error)?;
        Ok(Self(hwnd))
    }
}

impl Drop for Owner {
    fn drop(&mut self) {
        let _ = unsafe { DestroyWindow(self.0) };
    }
}

struct Access;

impl Access {
    fn open(owner: HWND) -> Result<Self, ProtocolError> {
        let deadline = Instant::now() + Duration::from_millis(500);
        loop {
            match unsafe { OpenClipboard(owner) } {
                Ok(()) => return Ok(Self),
                Err(error) if Instant::now() >= deadline => return Err(clipboard_error(error)),
                Err(_) => sleep(Duration::from_millis(10)),
            }
        }
    }
}

impl Drop for Access {
    fn drop(&mut self) {
        let _ = unsafe { CloseClipboard() };
    }
}

struct Data {
    format: u32,
    handle: HGLOBAL,
}

impl Data {
    fn allocate(format: u32, bytes: &[u8]) -> Result<Self, ProtocolError> {
        let handle = unsafe { GlobalAlloc(GMEM_MOVEABLE, bytes.len()) }.map_err(clipboard_error)?;
        let data = Self { format, handle };
        let ptr = unsafe { GlobalLock(handle) };
        if ptr.is_null() {
            return Err(clipboard_error("Cannot lock clipboard allocation"));
        }
        unsafe {
            std::ptr::copy_nonoverlapping(bytes.as_ptr(), ptr.cast(), bytes.len());
            let _ = GlobalUnlock(handle);
        }
        Ok(data)
    }

    fn copy(format: u32) -> Result<Self, ProtocolError> {
        let handle = unsafe { GetClipboardData(format) }.map_err(clipboard_error)?;
        let global = HGLOBAL(handle.0);
        let size = unsafe { GlobalSize(global) };
        if size == 0 || size > MAX_CLIPBOARD_BYTES {
            return Err(clipboard_error("Clipboard format is unreadable or exceeds 16 MiB"));
        }
        let ptr = unsafe { GlobalLock(global) };
        if ptr.is_null() {
            return Err(clipboard_error("Cannot lock existing clipboard data"));
        }
        let copy = unsafe {
            Self::allocate(format, std::slice::from_raw_parts(ptr.cast(), size))
        };
        let _ = unsafe { GlobalUnlock(global) };
        copy
    }

    fn publish(&mut self) -> Result<(), ProtocolError> {
        unsafe { SetClipboardData(self.format, HANDLE(self.handle.0)) }.map_err(clipboard_error)?;
        // Windows owns a published allocation. Do not free it on drop.
        self.handle = HGLOBAL::default();
        Ok(())
    }
}

impl Drop for Data {
    fn drop(&mut self) {
        if !self.handle.0.is_null() {
            let _ = unsafe { GlobalFree(self.handle) };
        }
    }
}

fn snapshot() -> Result<Vec<Data>, ProtocolError> {
    let mut saved = Vec::new();
    let mut format = 0;
    loop {
        unsafe { SetLastError(WIN32_ERROR(0)) };
        format = unsafe { EnumClipboardFormats(format) };
        if format == 0 {
            if unsafe { GetLastError() }.0 != 0 {
                return Err(clipboard_error("Cannot enumerate clipboard formats"));
            }
            return Ok(saved);
        }
        // Text, OEM text, Unicode text, and locale are HGLOBAL-backed formats.
        // Refuse rich text, images, private formats, and delayed objects rather
        // than claiming to preserve formats whose ownership we cannot copy.
        if !matches!(format, 1 | 7 | 13 | 16) {
            return Err(ProtocolError::new(
                "clipboard_unsupported", "Paste requires an empty or plain-text clipboard; existing contents were preserved",
            ));
        }
        saved.push(Data::copy(format)?);
    }
}

fn restore_data(saved: &mut [Data]) -> Result<(), ProtocolError> {
    unsafe { EmptyClipboard() }.map_err(clipboard_error)?;
    for data in saved {
        data.publish()?;
    }
    Ok(())
}

pub struct Clipboard {
    owner: Owner,
    saved: Vec<Data>,
    sequence: u32,
}

impl Clipboard {
    pub fn stage(text: &str) -> Result<Self, ProtocolError> {
        let bytes: Vec<u8> = text.encode_utf16().chain(std::iter::once(0))
            .flat_map(u16::to_le_bytes).collect();
        let mut data = Data::allocate(CF_UNICODETEXT, &bytes)?;
        let owner = Owner::new()?;
        let access = Access::open(owner.0)?;
        // Copy and allocate every original format before changing the clipboard.
        let mut saved = snapshot()?;
        unsafe { EmptyClipboard() }.map_err(clipboard_error)?;
        if let Err(mut error) = data.publish() {
            let restored = restore_data(&mut saved).is_ok();
            error.details = serde_json::json!({"clipboard_restored": restored, "events_injected": 0});
            return Err(error);
        }
        drop(access);
        // Closing publishes clipboard updates and synthesized text formats.
        // Sample the sequence only after publication. Ownership also guards
        // against another writer racing this post-close sample.
        let sequence = unsafe { GetClipboardSequenceNumber() };
        Ok(Self { owner, saved, sequence })
    }

    pub fn is_current(&self) -> bool {
        unsafe {
            GetClipboardSequenceNumber() == self.sequence
                && GetClipboardOwner().ok() == Some(self.owner.0)
        }
    }

    pub fn restore(&mut self) -> Result<&'static str, ProtocolError> {
        let _access = Access::open(self.owner.0)?;
        if !self.is_current() {
            return Ok("changed_by_other_process");
        }
        restore_data(&mut self.saved)?;
        Ok("restored")
    }
}
