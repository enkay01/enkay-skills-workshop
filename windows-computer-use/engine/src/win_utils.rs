use serde::{Deserialize, Serialize};
use windows::Win32::Foundation::{BOOL, FILETIME, HANDLE, HWND, LPARAM, RECT};
use windows::Win32::System::StationsAndDesktops::{
    CloseDesktop, GetUserObjectInformationW, OpenInputDesktop, SetThreadDesktop,
    DESKTOP_ACCESS_FLAGS, DESKTOP_CONTROL_FLAGS, UOI_NAME,
};
use windows::Win32::System::Threading::{
    GetProcessTimes, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
};
use windows::Win32::UI::HiDpi::GetDpiForWindow;
use windows::Win32::Graphics::Dwm::{DwmGetWindowAttribute, DWMWA_EXTENDED_FRAME_BOUNDS};
use windows::Win32::Graphics::Gdi::{
    EnumDisplayMonitors, GetMonitorInfoW, MonitorFromWindow, HDC, HMONITOR, MONITORINFO,
    MONITORINFOEXW, MONITOR_DEFAULTTONEAREST,
};
use windows::Win32::UI::HiDpi::{GetDpiForMonitor, MDT_EFFECTIVE_DPI};
use windows::Win32::UI::WindowsAndMessaging::{
    BringWindowToTop, EnumWindows, GetAncestor, GetClassNameW, GetForegroundWindow,
    GetWindowRect, GetWindowTextLengthW, GetWindowTextW, GetWindowThreadProcessId, IsIconic,
    IsWindow, IsWindowVisible, SetForegroundWindow, ShowWindow, GA_ROOT, SW_RESTORE,
};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WindowInfo {
    pub hwnd: String,
    pub pid: u32,
    pub process_create_time_utc: String,
    pub title: String,
    pub class_name: String,
    pub bounds: RectBounds,
    pub dpi: u32,
    pub is_foreground: bool,
    pub is_visible: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub monitor_handle: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct RectBounds {
    pub x: i32,
    pub y: i32,
    pub w: i32,
    pub h: i32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MonitorInfo {
    pub index: u32,
    pub device_name: String,
    pub hmonitor: String,
    pub bounds: RectBounds,
    pub dpi: u32,
    pub scale_factor: f64,
    pub is_primary: bool,
}

/// The desktop name Windows reports on a normal interactive session.
const INTERACTIVE_DESKTOP_NAME: &str = "Default";

/// Test control: when set to any value, [`check_interactive_desktop`] reports the
/// session as having no interactive desktop.
///
/// This exists because the real condition cannot be produced from a test without
/// changing the state of the live interactive session the suite runs in. Locking
/// the session removes the desktop the tests need; switching the input desktop
/// blanks the user's screen; and creating a non-interactive window station fails
/// with `ERROR_ACCESS_DENIED` because it needs `SE_CREATE_WINDOW_STATION`.
/// `CreateDesktop` plus `SetThreadDesktop` does not help either, because
/// `OpenInputDesktop` reports the *window station's* input desktop rather than the
/// calling thread's, so the name read back is still `Default`.
///
/// The valve is deliberately one-way. No value of it, and no code path, turns a
/// failing check into a passing one, so it cannot be used to weaken the guard: it
/// can only make the engine refuse. Setting it by accident in production
/// therefore makes every action fail loudly rather than letting input through.
///
/// It is honoured in release builds too. Restricting it to debug builds would let
/// a suite run against a release binary pass while asserting nothing.
///
/// See `specs/desktop-inaccessibility-guard-testing.md`, which records the
/// measurements behind each of those claims.
const TEST_DESKTOP_INACCESSIBLE_VAR: &str = "WCU_TEST_DESKTOP_INACCESSIBLE";

/// What the Win32 query learned about the input desktop.
///
/// Split out from the verdict so the decision can be reasoned about and tested
/// without the operating system state that a live refusal depends on.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum InputDesktop {
    /// The input desktop could not be opened at all. This is what a locked or
    /// headless session reports.
    Unavailable(String),
    /// The input desktop was opened but its name could not be read.
    NameUnavailable(String),
    /// The input desktop was opened and is named this.
    Named(String),
}

/// Decide whether the session is usable, from what the query found.
///
/// A name that is empty, padded, or otherwise not the interactive desktop is
/// refused rather than normalised. A name the engine did not expect exactly is a
/// reason to stop and report, not a reason to guess.
pub fn desktop_access_verdict(probe: &InputDesktop) -> Result<String, String> {
    match probe {
        InputDesktop::Unavailable(why) => Err(format!(
            "Cannot access the input desktop: the session is locked, disconnected or headless ({})",
            why
        )),
        InputDesktop::NameUnavailable(why) => {
            Err(format!("Opened the input desktop but could not read its name ({})", why))
        }
        InputDesktop::Named(name) => {
            if name.eq_ignore_ascii_case(INTERACTIVE_DESKTOP_NAME) {
                Ok(format!("WinSta0\\{} (Interactive)", name))
            } else {
                Err(format!(
                    "Desktop is '{}' (Expected '{}')",
                    name, INTERACTIVE_DESKTOP_NAME
                ))
            }
        }
    }
}

/// Query the window station's input desktop and reduce it to the facts the verdict
/// needs. Safe to call on any thread: it neither switches nor attaches.
fn probe_input_desktop() -> InputDesktop {
    unsafe {
        let h_desk = match OpenInputDesktop(DESKTOP_CONTROL_FLAGS(0), false, DESKTOP_ACCESS_FLAGS(0x0100)) {
            Ok(h) => h,
            Err(e) => return InputDesktop::Unavailable(format!("{}", e)),
        };

        let mut name_buf = [0u16; 256];
        let mut needed = 0u32;
        let ok = GetUserObjectInformationW(
            HANDLE(h_desk.0),
            UOI_NAME,
            Some(name_buf.as_mut_ptr() as *mut _),
            (name_buf.len() * 2) as u32,
            Some(&mut needed),
        );
        let _ = CloseDesktop(h_desk);

        if ok.is_err() {
            return InputDesktop::NameUnavailable(format!("{}", ok.unwrap_err()));
        }
        let len = (0..name_buf.len())
            .position(|i| name_buf[i] == 0)
            .unwrap_or(name_buf.len());
        InputDesktop::Named(String::from_utf16_lossy(&name_buf[..len]))
    }
}

pub fn check_interactive_desktop() -> Result<String, String> {
    // One-way valve: forces the refusing verdict and nothing else. Checked before
    // the query so a test never depends on the session being in a given state.
    if std::env::var_os(TEST_DESKTOP_INACCESSIBLE_VAR).is_some() {
        return Err(format!(
            "{} is set: reporting the session as having no interactive desktop \
             (test control, not a real condition)",
            TEST_DESKTOP_INACCESSIBLE_VAR
        ));
    }
    desktop_access_verdict(&probe_input_desktop())
}

pub fn attach_thread_to_input_desktop() -> Result<(), String> {
    unsafe {
        let h_desk = OpenInputDesktop(DESKTOP_CONTROL_FLAGS(0), false, DESKTOP_ACCESS_FLAGS(0x01FF));
        if let Ok(desk) = h_desk {
            let ok = SetThreadDesktop(desk);
            if ok.is_ok() {
                Ok(())
            } else {
                let _ = CloseDesktop(desk);
                Err("SetThreadDesktop failed".into())
            }
        } else {
            Err("OpenInputDesktop failed: session may be locked".into())
        }
    }
}

pub fn get_process_creation_time(pid: u32) -> String {
    unsafe {
        let handle = match OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid) {
            Ok(h) => h,
            Err(_) => return "unknown".into(),
        };

        let mut create_time = FILETIME::default();
        let mut exit_time = FILETIME::default();
        let mut kernel_time = FILETIME::default();
        let mut user_time = FILETIME::default();

        let ok = GetProcessTimes(
            handle,
            &mut create_time,
            &mut exit_time,
            &mut kernel_time,
            &mut user_time,
        );
        let _ = windows::Win32::Foundation::CloseHandle(handle);

        if ok.is_ok() {
            let u64_time = ((create_time.dwHighDateTime as u64) << 32) | (create_time.dwLowDateTime as u64);
            u64_time.to_string()
        } else {
            "unknown".into()
        }
    }
}

pub fn list_windows() -> Vec<WindowInfo> {
    let _ = attach_thread_to_input_desktop();
    let mut list = Vec::new();

    unsafe extern "system" fn enum_proc(hwnd: HWND, lparam: LPARAM) -> BOOL {
        let list_ptr = lparam.0 as *mut Vec<WindowInfo>;
        let list = unsafe { &mut *list_ptr };

        let visible = unsafe { IsWindowVisible(hwnd).as_bool() };
        let mut rect = RECT::default();
        let _ = unsafe { GetWindowRect(hwnd, &mut rect) };
        let w = rect.right - rect.left;
        let h = rect.bottom - rect.top;

        if visible && w > 0 && h > 0 {
            let mut pid = 0u32;
            unsafe { GetWindowThreadProcessId(hwnd, Some(&mut pid)) };

            let title_len = unsafe { GetWindowTextLengthW(hwnd) };
            let mut title_buf = vec![0u16; (title_len + 1) as usize];
            if title_len > 0 {
                unsafe { GetWindowTextW(hwnd, &mut title_buf) };
            }
            let title = String::from_utf16_lossy(&title_buf[..title_len as usize]);

            let mut class_buf = [0u16; 256];
            let class_len = unsafe { GetClassNameW(hwnd, &mut class_buf) };
            let class_name = String::from_utf16_lossy(&class_buf[..class_len as usize]);

            let dpi = unsafe { GetDpiForWindow(hwnd) };
            let fg = unsafe { GetForegroundWindow() == hwnd };
            let create_time = get_process_creation_time(pid);
            let hmon = unsafe { MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST) };
            let monitor_handle = if hmon.0.is_null() {
                None
            } else {
                Some((hmon.0 as usize).to_string())
            };

            list.push(WindowInfo {
                hwnd: (hwnd.0 as usize).to_string(),
                pid,
                process_create_time_utc: create_time,
                title,
                class_name,
                bounds: RectBounds {
                    x: rect.left,
                    y: rect.top,
                    w,
                    h,
                },
                dpi,
                is_foreground: fg,
                is_visible: visible,
                monitor_handle,
            });
        }

        BOOL(1)
    }

    unsafe {
        let _ = EnumWindows(Some(enum_proc), LPARAM(&mut list as *mut _ as isize));
    }

    list
}

pub fn list_monitors() -> Vec<MonitorInfo> {
    let _ = attach_thread_to_input_desktop();
    let mut list = Vec::new();

    unsafe extern "system" fn monitor_enum_proc(
        hmonitor: HMONITOR,
        _hdc: HDC,
        _rect: *mut RECT,
        lparam: LPARAM,
    ) -> BOOL {
        let list_ptr = lparam.0 as *mut Vec<MonitorInfo>;
        let list = unsafe { &mut *list_ptr };

        let mut mi = MONITORINFOEXW::default();
        mi.monitorInfo.cbSize = std::mem::size_of::<MONITORINFOEXW>() as u32;

        let ok = unsafe {
            GetMonitorInfoW(
                hmonitor,
                &mut mi as *mut MONITORINFOEXW as *mut MONITORINFO,
            )
        };

        if ok.as_bool() {
            let rect = mi.monitorInfo.rcMonitor;
            let w = rect.right - rect.left;
            let h = rect.bottom - rect.top;

            let mut dpi_x = 96u32;
            let mut dpi_y = 96u32;
            let _ = unsafe {
                GetDpiForMonitor(hmonitor, MDT_EFFECTIVE_DPI, &mut dpi_x, &mut dpi_y)
            };
            let dpi = if dpi_x > 0 { dpi_x } else { 96 };
            let scale_factor = (dpi as f64) / 96.0;

            let is_primary = (mi.monitorInfo.dwFlags & 1) != 0;

            let dev_len = (0..mi.szDevice.len())
                .position(|i| mi.szDevice[i] == 0)
                .unwrap_or(mi.szDevice.len());
            let device_name = String::from_utf16_lossy(&mi.szDevice[..dev_len]);

            let index = list.len() as u32;

            list.push(MonitorInfo {
                index,
                device_name,
                hmonitor: (hmonitor.0 as usize).to_string(),
                bounds: RectBounds {
                    x: rect.left,
                    y: rect.top,
                    w,
                    h,
                },
                dpi,
                scale_factor,
                is_primary,
            });
        }

        BOOL(1)
    }

    unsafe {
        let _ = EnumDisplayMonitors(
            HDC::default(),
            None,
            Some(monitor_enum_proc),
            LPARAM(&mut list as *mut _ as isize),
        );
    }

    list
}

pub fn validate_window_identity(hwnd_val: usize, expected_pid: u32, expected_create_time: &str) -> Result<HWND, String> {
    let hwnd = HWND(hwnd_val as *mut _);
    unsafe {
        if !IsWindow(hwnd).as_bool() {
            return Err("Window handle does not exist".into());
        }

        let mut actual_pid = 0u32;
        GetWindowThreadProcessId(hwnd, Some(&mut actual_pid));
        if actual_pid != expected_pid {
            return Err(format!("PID mismatch: expected {}, got {}", expected_pid, actual_pid));
        }

        let actual_create_time = get_process_creation_time(actual_pid);
        if expected_create_time != "unknown" && actual_create_time != expected_create_time {
            return Err("Process creation time mismatch: process recycled".into());
        }

        Ok(hwnd)
    }
}

pub fn get_window_extended_frame_bounds(hwnd: HWND) -> Result<RectBounds, String> {
    unsafe {
        let mut rect = RECT::default();
        let hr = DwmGetWindowAttribute(
            hwnd,
            DWMWA_EXTENDED_FRAME_BOUNDS,
            &mut rect as *mut _ as *mut _,
            std::mem::size_of::<RECT>() as u32,
        );

        if hr.is_ok() {
            let w = rect.right - rect.left;
            let h = rect.bottom - rect.top;
            Ok(RectBounds {
                x: rect.left,
                y: rect.top,
                w,
                h,
            })
        } else {
            // Fallback to GetWindowRect
            let mut wrect = RECT::default();
            if GetWindowRect(hwnd, &mut wrect).is_ok() {
                Ok(RectBounds {
                    x: wrect.left,
                    y: wrect.top,
                    w: wrect.right - wrect.left,
                    h: wrect.bottom - wrect.top,
                })
            } else {
                Err("Failed to query window bounds".into())
            }
        }
    }
}

/// A reference to a window, used to report which window actually holds the
/// foreground when a focus request is refused.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WindowRef {
    pub hwnd: String,
    pub pid: u32,
    pub title: String,
    pub class_name: String,
}

/// A small, stable description of a window for error reporting.
pub fn describe_window(hwnd_val: usize) -> Option<WindowRef> {
    let hwnd = HWND(hwnd_val as *mut _);
    if hwnd.0.is_null() || !unsafe { IsWindow(hwnd) }.as_bool() {
        return None;
    }
    let (title, pid) = {
        let mut pid = 0u32;
        unsafe {
            GetWindowThreadProcessId(hwnd, Some(&mut pid));
            let title_len = GetWindowTextLengthW(hwnd);
            let mut buf = vec![0u16; (title_len + 1) as usize];
            if title_len > 0 {
                GetWindowTextW(hwnd, &mut buf);
            }
            (String::from_utf16_lossy(&buf[..title_len as usize]), pid)
        }
    };
    let class_name = unsafe {
        let mut buf = [0u16; 256];
        let len = GetClassNameW(hwnd, &mut buf);
        String::from_utf16_lossy(&buf[..len as usize])
    };
    Some(WindowRef {
        hwnd: hwnd_val.to_string(),
        pid,
        title,
        class_name,
    })
}

/// The window that currently holds the foreground, and its root ancestor.
pub fn current_foreground() -> (usize, usize) {
    unsafe {
        let fg = GetForegroundWindow();
        let root = GetAncestor(fg, GA_ROOT);
        (fg.0 as usize, root.0 as usize)
    }
}

/// Restore a window if it is minimized, bring it to the top, request the
/// foreground, and then verify the result against the actual foreground window
/// rather than trusting the request.
///
/// This uses only ordinary, documented window-management calls. It does not use
/// simulated modifier keystrokes or any other focus-stealing workaround, so a
/// window that refuses foreground produces an honest refusal.
pub fn focus_window_verified(
    hwnd_val: usize,
    expected_pid: Option<u32>,
    expected_create_time: Option<&str>,
) -> Result<FocusOutcome, FocusProbe> {
    let hwnd = HWND(hwnd_val as *mut _);

    if !unsafe { IsWindow(hwnd) }.as_bool() {
        return Err(FocusProbe {
            code: "window_gone".into(),
            message: "Window handle does not exist".into(),
            actual_foreground: None,
        });
    }

    if let Some(pid) = expected_pid {
        let mut actual_pid = 0u32;
        unsafe {
            GetWindowThreadProcessId(hwnd, Some(&mut actual_pid));
        }
        if actual_pid != pid {
            return Err(FocusProbe {
                code: "window_gone".into(),
                message: format!("PID mismatch: expected {}, got {}", pid, actual_pid),
                actual_foreground: None,
            });
        }
        if let Some(ct) = expected_create_time {
            if ct != "unknown" {
                let actual = get_process_creation_time(actual_pid);
                if actual != ct {
                    return Err(FocusProbe {
                        code: "window_gone".into(),
                        message: "Process creation time mismatch: process recycled".into(),
                        actual_foreground: None,
                    });
                }
            }
        }
    }

    let (previous_foreground, _) = current_foreground();

    // Restore first, so that switching does not fail on a minimized target.
    let was_minimized = unsafe { IsIconic(hwnd) }.as_bool();
    if was_minimized {
        let _ = unsafe { ShowWindow(hwnd, SW_RESTORE) };
    }
    let _ = unsafe { BringWindowToTop(hwnd) };
    let _ = unsafe { SetForegroundWindow(hwnd) };
    // Give the window manager a moment to settle before verifying.
    std::thread::sleep(std::time::Duration::from_millis(120));

    let (fg, fg_root) = current_foreground();
    if fg != hwnd_val && fg_root != hwnd_val {
        return Err(FocusProbe {
            code: "focus_refused".into(),
            message: format!(
                "Window {} refused foreground; {:?} holds it",
                hwnd_val, fg
            ),
            actual_foreground: describe_window(fg),
        });
    }

    Ok(FocusOutcome {
        hwnd: hwnd_val.to_string(),
        requested: describe_window(hwnd_val),
        previous_foreground: describe_window(previous_foreground),
        foreground: describe_window(fg),
        restored_from_minimized: was_minimized,
        verified: true,
    })
}

/// A refused focus, carrying the window that actually holds the foreground.
#[derive(Debug, Clone)]
pub struct FocusProbe {
    pub code: String,
    pub message: String,
    pub actual_foreground: Option<WindowRef>,
}

/// The verified result of a focus request.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FocusOutcome {
    pub hwnd: String,
    pub requested: Option<WindowRef>,
    pub previous_foreground: Option<WindowRef>,
    pub foreground: Option<WindowRef>,
    pub restored_from_minimized: bool,
    pub verified: bool,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_enum_windows() {
        let list = list_windows();
        println!("test_enum_windows found: {} windows", list.len());
        for w in list.iter().take(5) {
            println!("  window: {} ({}) [{}]", w.title, w.class_name, w.hwnd);
        }
    }

    // --- input desktop verdict -------------------------------------------------
    //
    // These cover the decision only, because the state that makes the check fail
    // cannot be produced from a test without changing the live session. See the
    // comment on TEST_DESKTOP_INACCESSIBLE_VAR for what was tried.

    /// The valve lives in the process environment, which `cargo test` shares across
    /// concurrently running tests. Every test that sets, removes, or asserts its
    /// absence takes this first, so the suite is deterministic without needing
    /// `--test-threads=1`.
    static VALVE_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

    fn lock_valve() -> std::sync::MutexGuard<'static, ()> {
        // A previous test panicking while holding the lock poisons it; the state
        // it guards is the environment, which the next test re-establishes anyway.
        VALVE_LOCK.lock().unwrap_or_else(|e| e.into_inner())
    }

    #[test]
    fn verdict_accepts_the_interactive_desktop() {
        let r = desktop_access_verdict(&InputDesktop::Named("Default".into()));
        assert!(r.is_ok(), "Default must be accepted: {:?}", r);
    }

    #[test]
    fn verdict_ignores_ascii_case() {
        for name in ["default", "DEFAULT", "DeFaUlT"] {
            let r = desktop_access_verdict(&InputDesktop::Named(name.into()));
            assert!(r.is_ok(), "{} must be accepted: {:?}", name, r);
        }
    }

    #[test]
    fn verdict_refuses_the_other_desktops_windows_names() {
        // Winlogon and Screen-saver are the names Windows itself uses, and both
        // appear while a session is logging on or locking.
        for name in ["Winlogon", "Screen-saver", "Service-0x0-3e7$"] {
            let r = desktop_access_verdict(&InputDesktop::Named(name.into()));
            assert!(r.is_err(), "{} must be refused, got {:?}", name, r);
        }
    }

    #[test]
    fn verdict_refuses_a_non_interactive_desktop() {
        let r = desktop_access_verdict(&InputDesktop::Named("Locked".into()));
        assert!(r.is_err(), "a non-default desktop must be refused, got {:?}", r);
        let msg = r.unwrap_err();
        assert!(msg.contains("Locked"), "the message must name the desktop found: {}", msg);
        assert!(msg.contains("Default"), "the message must name the one expected: {}", msg);
    }

    #[test]
    fn verdict_refuses_rather_than_normalising_an_unexpected_name() {
        // Empty and padded names are refused, not trimmed. A name the engine did
        // not expect exactly is a reason to stop and report, not to guess.
        for name in ["", " ", " Default", "Default ", "Default\t"] {
            let r = desktop_access_verdict(&InputDesktop::Named(name.into()));
            assert!(r.is_err(), "{:?} must be refused, got {:?}", name, r);
        }
    }

    #[test]
    fn verdict_refuses_an_unopened_or_unnamed_desktop() {
        let r = desktop_access_verdict(&InputDesktop::Unavailable("access denied".into()));
        assert!(r.is_err(), "an unopened input desktop must be refused, got {:?}", r);
        assert!(r.unwrap_err().contains("access denied"));

        let r = desktop_access_verdict(&InputDesktop::NameUnavailable("bad handle".into()));
        assert!(r.is_err(), "an unreadable name must be refused, got {:?}", r);
        assert!(r.unwrap_err().contains("bad handle"));
    }

    // --- the live Win32 query --------------------------------------------------

    #[test]
    fn live_input_desktop_is_readable_and_interactive() {
        let _guard = lock_valve();
        // The one branch a live interactive session can honestly produce. It
        // exercises the real API usage, which the pure verdict tests cannot.
        match probe_input_desktop() {
            InputDesktop::Named(name) => assert!(
                name.eq_ignore_ascii_case(INTERACTIVE_DESKTOP_NAME),
                "expected the interactive desktop, got {:?}",
                name
            ),
            other => panic!(
                "expected a named input desktop on an interactive session, got {:?}",
                other
            ),
        }
        assert!(
            check_interactive_desktop().is_ok(),
            "the guard's own check must pass on an interactive session"
        );
    }

    // --- the one-way valve -----------------------------------------------------

    #[test]
    fn the_valve_only_ever_forces_a_refusal() {
        let _guard = lock_valve();
        // Unset: the real verdict stands, and on this machine it is interactive.
        assert!(std::env::var_os(TEST_DESKTOP_INACCESSIBLE_VAR).is_none());
        assert!(check_interactive_desktop().is_ok());

        // Set to values that could plausibly mean "off" if the valve were a
        // boolean. It is not: any value at all forces the refusal, and the
        // refusal names the control so it can never be mistaken for a real
        // locked session.
        for value in ["1", "0", "", "false"] {
            std::env::set_var(TEST_DESKTOP_INACCESSIBLE_VAR, value);
            let r = check_interactive_desktop();
            std::env::remove_var(TEST_DESKTOP_INACCESSIBLE_VAR);
            assert!(
                r.is_err(),
                "{:?} must force a refusal, got {:?}",
                value,
                r
            );
            assert!(
                r.unwrap_err().contains(TEST_DESKTOP_INACCESSIBLE_VAR),
                "the message must name the test control that decided this"
            );
        }
    }
}


