import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

hdesk = user32.OpenInputDesktop(0, False, 0x0100)
print("OpenInputDesktop:", hdesk)
if hdesk:
    buf = ctypes.create_unicode_buffer(256)
    needed = wintypes.DWORD()
    ok = user32.GetUserObjectInformationW(hdesk, 2, buf, 512, ctypes.byref(needed))
    print("Desktop name:", buf.value if ok else "failed")
    user32.CloseDesktop(hdesk)
else:
    err = kernel32.GetLastError()
    print("OpenInputDesktop failed, error code:", err)

# Check thread desktop
thread_desk = user32.GetThreadDesktop(kernel32.GetCurrentThreadId())
print("GetThreadDesktop:", thread_desk)
if thread_desk:
    buf = ctypes.create_unicode_buffer(256)
    needed = wintypes.DWORD()
    ok = user32.GetUserObjectInformationW(thread_desk, 2, buf, 512, ctypes.byref(needed))
    print("Thread desktop name:", buf.value if ok else "failed")

# Switch thread desktop to OpenInputDesktop
hdesk_input = user32.OpenInputDesktop(0, False, 0x01FF)
print("OpenInputDesktop 0x01FF:", hdesk_input)
if hdesk_input:
    set_ok = user32.SetThreadDesktop(hdesk_input)
    print("SetThreadDesktop to InputDesktop:", set_ok)
    if not set_ok:
        print("SetThreadDesktop failed:", kernel32.GetLastError())

# Check window enumeration
windows = []
def enum_cb(hwnd, lparam):
    if user32.IsWindowVisible(hwnd):
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        windows.append((hwnd, buf.value))
    return True

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
print(f"Total visible windows enumerated: {len(windows)}")
for h, t in windows[:10]:
    if t:
        print(f"  [{h}] '{t}'")
