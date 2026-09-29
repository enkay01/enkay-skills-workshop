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
    BringWindowToTop, EnumWindows, GetClassNameW, GetForegroundWindow, GetWindowRect,
    GetWindowTextLengthW, GetWindowTextW, GetWindowThreadProcessId, IsWindow, IsWindowVisible,
    SetForegroundWindow, ShowWindow, SW_RESTORE,
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

pub fn check_interactive_desktop() -> Result<String, String> {
    unsafe {
        let h_desk = OpenInputDesktop(DESKTOP_CONTROL_FLAGS(0), false, DESKTOP_ACCESS_FLAGS(0x0100));
        if h_desk.is_err() {
            return Err("Cannot access input desktop: session is locked or headless".into());
        }
        let h_desk = h_desk.unwrap();

        let mut name_buf = [0u16; 256];
        let mut needed = 0u32;
        let handle = HANDLE(h_desk.0);
        let ok = GetUserObjectInformationW(
            handle,
            UOI_NAME,
            Some(name_buf.as_mut_ptr() as *mut _),
            (name_buf.len() * 2) as u32,
            Some(&mut needed),
        );
        let _ = CloseDesktop(h_desk);

        if ok.is_ok() {
            let len = (0..name_buf.len())
                .position(|i| name_buf[i] == 0)
                .unwrap_or(name_buf.len());
            let name = String::from_utf16_lossy(&name_buf[..len]);
            if name.eq_ignore_ascii_case("default") {
                Ok("WinSta0\\Default (Interactive)".into())
            } else {
                Err(format!("Desktop is '{}' (Expected 'Default')", name))
            }
        } else {
            Err("Failed to query input desktop name".into())
        }
    }
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

pub fn focus_window(hwnd_val: usize) -> Result<(), String> {
    let hwnd = HWND(hwnd_val as *mut _);
    unsafe {
        if !IsWindow(hwnd).as_bool() {
            return Err("Window handle does not exist".into());
        }
        let _ = ShowWindow(hwnd, SW_RESTORE);
        let _ = BringWindowToTop(hwnd);
        let ok = SetForegroundWindow(hwnd);
        if ok.as_bool() || GetForegroundWindow() == hwnd {
            Ok(())
        } else {
            Err("Failed to set target window as foreground".into())
        }
    }
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
}


