"""Phase 7 Verification Suite: Desktop Actions Beyond Click.

Every action is exercised through the single existing seam: this client driving
the persistent Rust engine against a real native window on a real interactive
desktop. Each test asserts what the *application* did, read back through the
existing inspection operation, rather than what the engine returned. A dispatched
event is never treated as a completed one.

Covered actions: typing, key chords, scrolling (vertical and horizontal, targeted
and untargeted), hover, drag, right click, double click, and explicit window
focus and switching.

Shared refusals are proven for typing, chords and scrolling (the actions where the
guard is reachable with a different argument shape), plus a comprehensive set
against hover and drag.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
from ctypes import wintypes
import os
import re
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
client_dir = os.path.join(root_dir, "client")
if client_dir not in sys.path:
    sys.path.insert(0, client_dir)

from wcu_client import WcuClient, WcuError

user32 = ctypes.windll.user32

DPI_AWARENESS_CONTEXT_UNAWARE = -1
DPI_AWARENESS_CONTEXT_SYSTEM_AWARE = -2
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE = -3
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4

user32.SetProcessDpiAwarenessContext.restype = ctypes.c_bool
user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
user32.AreDpiAwarenessContextsEqual.restype = ctypes.c_bool
user32.AreDpiAwarenessContextsEqual.argtypes = [ctypes.c_void_p, ctypes.c_void_p]


def _as_handle(context: int) -> ctypes.c_void_p:
    return ctypes.c_void_p(context & 0xFFFFFFFF)


def _context_is(context: int) -> bool:
    got = user32.GetThreadDpiAwarenessContext()
    if not got:
        return False
    return bool(user32.AreDpiAwarenessContextsEqual(_as_handle(got), _as_handle(context)))


def _become_dpi_aware() -> str:
    """Best-effort per-monitor DPI awareness, for reporting only.

    A manifest may already have fixed the process context, in which case neither
    the process-wide nor the per-thread call succeeds. Rather than depend on it,
    the coordinate scale this process actually uses is measured at run time; see
    `TestPhase7DesktopActions._measure_coordinate_scale`.
    """
    user32.SetProcessDpiAwarenessContext(
        _as_handle(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
    )
    user32.SetThreadDpiAwarenessContext(
        _as_handle(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
    )
    if _context_is(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
        return "per_monitor_v2"
    if _context_is(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE):
        return "per_monitor"
    return "inherited_from_manifest"


DPI_CONTEXT = _become_dpi_aware()

FIXTURE_EXE = os.path.abspath(
    os.path.join(
        root_dir, "tests", "fixture_app", "bin", "Debug", "net6.0-windows", "fixture_app.exe"
    )
)

ACTION_WINDOW_TITLE = "WCU_Native_Action_Fixture"
PRIMARY_WINDOW_TITLE = "WCU_Native_WinForms_Fixture"

# Window messages the action fixture handles to lock and unlock the foreground it
# owns. These must match the constants in ActionForm.cs.
WM_APP_LOCK_FOREGROUND = 0x8003
WM_APP_UNLOCK_FOREGROUND = 0x8004

PM_REMOVE = 0x0001
LSFW_LOCK = 0x0001
LSFW_UNLOCK = 0x0002


class _CoverWindow:
    """A visible topmost window owned by this process, on its own pumping thread.

    Topmost guarantees it sits above the fixture, which is not topmost, so the
    occlusion it creates is a fact rather than an assumption.
    """

    def __init__(self, x: int, y: int, w: int, h: int) -> None:
        self.x, self.y, self.w, self.h = x, y, w, h
        self.hwnd = 0
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self, timeout: float = 5.0) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise RuntimeError("Cover window thread did not start")
        if not self.hwnd:
            raise RuntimeError("Failed to create the cover window")

    def _run(self) -> None:
        # WS_EX_NOACTIVATE keeps the cover from taking the foreground when it is
        # shown, so the fixture stays the foreground window and a refusal is
        # attributable to occlusion rather than to a foreground change.
        hwnd = _create_owned_window(
            WS_EX_TOPMOST | WS_EX_NOACTIVATE, WS_POPUP_VISIBLE, self.x, self.y, self.w, self.h
        )
        self.hwnd = int(hwnd) if hwnd else 0
        if self.hwnd:
            user32.SetWindowPos(
                ctypes.c_void_p(self.hwnd), HWND_TOPMOST, self.x, self.y,
                self.w, self.h, SWP_SHOWWINDOW | SWP_NOACTIVATE,
            )
        self._ready.set()
        _pump_until(self._stop)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def root_at(self, x: int, y: int) -> int:
        """The root owner of the window at a physical screen point."""
        hwnd_at = user32.WindowFromPoint(wintypes.POINT(x, y))
        return user32.GetAncestor(hwnd_at, GA_ROOT) or 0


WS_EX_TOPMOST = 0x00000008
WS_EX_NOACTIVATE = 0x08000000
HWND_TOPMOST = ctypes.c_void_p(-1)
SWP_SHOWWINDOW = 0x0040
SWP_NOACTIVATE = 0x0010
GA_ROOT = 2
WS_POPUP = 0x80000000
WS_POPUP_VISIBLE = 0x80000000 | 0x10000000
GENERIC_ALL = 0x10000000


def _occluding_root(message: str) -> int:
    """Pull the occluding root window handle out of a `target_occluded` message.

    The engine reports the window it hit-tested as `0x`-prefixed hex, so the
    value is parsed rather than substring-matched: a decimal comparison against a
    hex message would pass or fail for the wrong reason.
    """
    match = re.search(r"\(root (0x[0-9a-fA-F]+|None)\)", message)
    if not match:
        raise AssertionError(f"No occluding root window reported in: {message!r}")
    if match.group(1) == "None":
        return 0
    return int(match.group(1), 16)


def _create_owned_window(ex_style: int, style: int, x: int, y: int, w: int, h: int):
    """Create a window on the calling thread. Call from a pumping thread.

    The coordinates are in the coordinate space of the creating thread, which is
    not necessarily physical; the caller converts using the measured scale.
    """
    user32.CreateWindowExW.restype = ctypes.c_void_p
    user32.CreateWindowExW.argtypes = [
        ctypes.c_ulong, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    ]
    return user32.CreateWindowExW(
        ex_style, "STATIC", "Cover", style, x, y, w, h, None, None, None, None
    )


def _pump_until(stop: threading.Event) -> None:
    user32.PeekMessageW.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint
    ]
    msg = ctypes.wintypes.MSG()
    while not stop.is_set():
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.005)


class _OffDesktopWindow:
    """A real window on a private desktop, kept for the coverage it documents.

    This is no longer how a focus refusal is produced. A window that is not on
    the input desktop is rejected as ``window_gone`` during the engine's window
    lookup, so it never reaches the focus attempt and proves nothing about focus
    refusal. The refusal is exercised instead by a real foreground lock; see
    ``test_27_refused_focus_names_the_window_that_holds_the_foreground``.

    It is retained because it is the clearest demonstration of that distinction,
    and a future change to window lookup should not quietly start reporting a
    different code for an off-desktop handle.
    """

    def __init__(self) -> None:
        self.hwnd = 0
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self, timeout: float = 5.0) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise RuntimeError("Off-desktop window thread did not start")
        if not self.hwnd:
            raise RuntimeError("Failed to create the off-desktop window")

    def _run(self) -> None:
        user32.CreateDesktopW.restype = ctypes.c_void_p
        user32.CreateDesktopW.argtypes = [
            ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong,
            ctypes.c_ulong, ctypes.c_void_p,
        ]
        desktop = user32.CreateDesktopW("wcu_refuse_desk", None, None, 0, GENERIC_ALL, None)
        if not desktop:
            self._ready.set()
            return
        if not user32.SetThreadDesktop(ctypes.c_void_p(desktop)):
            user32.CloseDesktop(ctypes.c_void_p(desktop))
            self._ready.set()
            return
        hwnd = _create_owned_window(0, WS_POPUP_VISIBLE, 80, 80, 300, 140)
        self.hwnd = int(hwnd) if hwnd else 0
        if self.hwnd:
            user32.ShowWindow(ctypes.c_void_p(self.hwnd), 5)
        self._ready.set()
        _pump_until(self._stop)
        if self.hwnd:
            user32.DestroyWindow(ctypes.c_void_p(self.hwnd))
        user32.CloseDesktop(ctypes.c_void_p(desktop))

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)


class TestPhase7DesktopActions(unittest.TestCase):
    client: WcuClient
    action_proc: subprocess.Popen
    primary_proc: subprocess.Popen
    action_window: dict
    primary_window: dict

    # ------------------------------------------------------------------
    # Fixture lifecycle
    # ------------------------------------------------------------------

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(FIXTURE_EXE):
            raise FileNotFoundError(f"Fixture executable not found at '{FIXTURE_EXE}'")

        hdesk = user32.OpenInputDesktop(0, False, 0x01FF)
        if hdesk:
            user32.SetThreadDesktop(hdesk)

        # The action window, and the original window as the switch-away target.
        cls.action_proc = subprocess.Popen([FIXTURE_EXE, "--actions"])
        cls.primary_proc = subprocess.Popen([FIXTURE_EXE])

        cls.client = WcuClient()
        cls.client.start()

        # Poll rather than sleep a fixed amount: two windows starting at once is a
        # race, and a fixed delay makes the suite fail for a reason that has
        # nothing to do with the behaviour under test.
        cls.action_window = cls._wait_for_window(ACTION_WINDOW_TITLE, cls.action_proc)
        cls.primary_window = cls._wait_for_window(PRIMARY_WINDOW_TITLE, cls.primary_proc)

        print(
            f"\n[Phase 7] Action window HWND {cls.action_window['hwnd']}, "
            f"primary window HWND {cls.primary_window['hwnd']}"
        )

        cls._attach_and_foreground(cls.action_window)
        cls.coord_scale = cls._measure_coordinate_scale(cls.action_window)
        print(
            f"\n[Phase 7] DPI context {DPI_CONTEXT}, "
            f"window coordinate scale {cls.coord_scale:.4f}"
        )

    @classmethod
    def _measure_coordinate_scale(cls, window: dict) -> float:
        """How far this process's window coordinates are virtualised, if at all.

        The engine is per-monitor DPI aware, so the bounds it reports are
        physical. If this process is system-aware, its own window coordinates are
        scaled by 96/dpi, and a window placed at an element's physical bounds
        would land somewhere else entirely. Rather than assume either way, the
        two are compared directly.
        """
        engine = window.get("bounds", {})
        rect = wintypes.RECT()
        user32.GetWindowRect(ctypes.c_void_p(int(window["hwnd"])), ctypes.byref(rect))
        local_w = rect.right - rect.left
        if not local_w or not engine.get("w"):
            return 1.0
        return local_w / engine["w"]

    def _to_local(self, x: int, y: int, w: int, h: int):
        """Convert physical pixels into this process's window coordinate space."""
        s = self.coord_scale
        return (round(x * s), round(y * s), round(w * s), round(h * s))

    @classmethod
    def _wait_for_window(cls, title: str, proc: subprocess.Popen, timeout: float = 20.0) -> dict:
        """Wait for a titled window to appear, reporting clearly if it never does."""
        deadline = time.time() + timeout
        last_titles: list = []
        while time.time() < deadline:
            windows = cls.client.list_windows()
            match = next((w for w in windows if title in w.get("title", "")), None)
            if match:
                return match
            last_titles = [w.get("title") for w in windows]
            if proc.poll() is not None:
                raise RuntimeError(
                    f"Fixture exited with code {proc.returncode} before '{title}' appeared"
                )
            time.sleep(0.25)
        raise RuntimeError(f"'{title}' did not appear within {timeout}s; saw {last_titles[:20]}")

    @classmethod
    def tearDownClass(cls):
        try:
            cls.client.stop()
        except Exception:
            pass
        for proc in (cls.action_proc, cls.primary_proc):
            try:
                if proc and proc.poll() is None:
                    proc.terminate()
                    proc.wait(timeout=2.0)
            except Exception:
                pass

    @classmethod
    def _attach_and_foreground(cls, window: dict) -> None:
        """Attach the engine to a window and make sure it is in the foreground."""
        cls.client.attach(
            window["hwnd"], window["pid"], window["process_create_time_utc"]
        )
        hwnd_int = int(window["hwnd"])
        user32.SetForegroundWindow(hwnd_int)
        time.sleep(0.4)

    # ------------------------------------------------------------------
    # Helpers: read the application's own state
    # ------------------------------------------------------------------

    def _elements(self) -> list:
        elements, _ = self.client.inspect(max_depth=8, max_elements=200)
        return elements

    def _by_id(self, automation_id: str) -> dict:
        el = next((e for e in self._elements() if e.get("automation_id") == automation_id), None)
        self.assertIsNotNone(el, f"{automation_id} not found in the accessibility tree")
        return el

    def _readout(self, automation_id: str) -> str:
        """Read a fixture label's text, i.e. the application's own reported state."""
        return self._by_id(automation_id).get("name", "")

    def _field_text(self, automation_id: str = "txt_target") -> str:
        """Read an editable field's value."""
        return self._by_id(automation_id).get("value", "")

    def _frame_target(self, automation_id: str, obs_meta: dict, padding: int = 0) -> list:
        """Convert an element's screen bounds into observed-frame pixels.

        This is how a caller translates something it saw in the image into an
        action: the element is located in the accessibility tree, then expressed
        in the coordinate system of the frame that was observed.
        """
        el = self._by_id(automation_id)
        b = el["bounds"]
        cb = obs_meta["capture_bounds_physical_px"]
        sx = obs_meta["width"] / cb["w"]
        sy = obs_meta["height"] / cb["h"]
        x = int((b["x"] - cb["x"]) * sx) + padding
        y = int((b["y"] - cb["y"]) * sy) + padding
        w = max(2, int(b["w"] * sx) - 2 * padding)
        h = max(2, int(b["h"] * sy) - 2 * padding)
        return [x, y, w, h]

    def _scroll_offset(self, axis: str = "v") -> int:
        """Read the fixture's own scroll offset readout for one axis."""
        recorded = self._readout("lbl_scroll")
        for part in recorded.split():
            if part.startswith(f"{axis}="):
                return int(part.split("=")[1])
        self.fail(f"Could not parse {axis} offset from {recorded!r}")

    def _observe(self) -> dict:
        """Bring the action window forward and observe a fresh frame."""
        self._assert_foreground()
        meta, _ = self.client.observe(timeout_ms=2000)
        return meta

    def _wait_until(self, predicate, message: str, timeout: float = 10.0) -> None:
        """Wait for a condition, failing with `message` rather than a bare timeout."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return
            time.sleep(0.1)
        self.fail(message)

    def _assert_foreground(self) -> None:
        """Re-assert the foreground immediately before acting.

        The engine refuses to inject while another window is in front, so this is
        checked right before every action rather than trusted from set-up.
        """
        hwnd_int = int(self.action_window["hwnd"])
        for _ in range(10):
            if user32.GetForegroundWindow() == hwnd_int:
                return
            user32.SetForegroundWindow(hwnd_int)
            time.sleep(0.1)
        self.assertEqual(
            user32.GetForegroundWindow(),
            hwnd_int,
            "Could not bring the fixture window to the foreground",
        )

    # ------------------------------------------------------------------
    # 1. Capability discovery
    # ------------------------------------------------------------------

    def test_01_capability_report_lists_the_action_vocabulary(self):
        """A client learns the action names without hard-coding them."""
        actions = self.client.actions()
        for name in (
            "click", "type_text", "press_key", "scroll", "hover", "drag", "focus_window",
        ):
            self.assertIn(name, actions, f"'{name}' missing from the capability report")
        print(f"\n[Test 1] Engine reports actions: {actions}")

    # ------------------------------------------------------------------
    # 2. Typing
    # ------------------------------------------------------------------

    def test_02_typing_fills_the_field(self):
        """Positive gate: a typed string is verified by reading the field's value."""
        meta = self._observe()
        # Put the caret in the field first, so the keystrokes have somewhere to go.
        user32.SetForegroundWindow(int(self.action_window["hwnd"]))
        time.sleep(0.2)
        field = self._frame_target("txt_target", meta)
        click_res = self.client.click(
            observation_id=meta["observation_id"],
            target_bbox_frame_px=field,
            max_age_ms=2000,
        )
        self.assertEqual(click_res.get("status"), "clicked")
        time.sleep(0.2)

        text = "Hello Desktop 42"
        res = self.client.type_text(text, delay_ms=6, timeout_sec=20.0)
        self.assertEqual(res.get("status"), "typed")
        self.assertEqual(res.get("characters_sent"), len(text))
        self.assertEqual(res.get("events_injected"), len(text) * 2)
        print(f"\n[Test 2] type_text dispatched: {res}")

        time.sleep(0.4)
        self.assertEqual(self._field_text(), text, "The field did not receive the typed text")
        print(f"[Test 2] Verified field value: {self._field_text()!r}")

    def test_03_typing_includes_characters_off_the_keyboard_layout(self):
        """Positive gate: accented and non-Latin text arrives intact."""
        self._assert_foreground()
        text = "café 日本語"
        res = self.client.type_text(text, delay_ms=8, timeout_sec=20.0)
        self.assertEqual(res.get("status"), "typed")
        # Non-BMP characters would add surrogate code units beyond the char count.
        print(f"\n[Test 3] type_text dispatched: {res}")

        time.sleep(0.5)
        self.assertEqual(
            self._field_text(), "Hello Desktop 42" + text,
            "Accented and non-Latin characters were dropped or substituted",
        )
        print(f"[Test 3] Verified field value: {self._field_text()!r}")

    def test_04_typing_a_non_bmp_character_survives_as_a_surrogate_pair(self):
        """A character outside the Basic Multilingual Plane is sent as a pair."""
        self._assert_foreground()
        res = self.client.type_text("\U0001F600", delay_ms=8, timeout_sec=20.0)
        self.assertEqual(res.get("status"), "typed")
        self.assertEqual(res.get("characters_sent"), 1)
        self.assertEqual(res.get("code_units_sent"), 2, "Expected a surrogate pair")
        self.assertEqual(res.get("events_injected"), 4)
        print(f"\n[Test 4] Non-BMP typing dispatched: {res}")

        time.sleep(0.5)
        self.assertTrue(
            self._field_text().endswith("\U0001F600"),
            f"Non-BMP character did not arrive intact: {self._field_text()!r}",
        )
        print(f"[Test 4] Verified field value: {self._field_text()!r}")

    # ------------------------------------------------------------------
    # 3. Key chords
    # ------------------------------------------------------------------

    def test_05_key_chord_is_recognised_by_the_application(self):
        """Positive gate: a chord is verified by the fixture's own key readout."""
        self._assert_foreground()
        res = self.client.press_key("ctrl+shift+s")
        self.assertEqual(res.get("status"), "key_sent")
        self.assertEqual(res.get("modifiers"), 2)
        print(f"\n[Test 5] press_key dispatched: {res}")

        time.sleep(0.3)
        recorded = self._readout("lbl_keys")
        self.assertTrue(recorded.lower().startswith("keys: ctrl"), f"Ctrl missing: {recorded}")
        self.assertIn("shift", recorded.lower(), f"Shift missing: {recorded}")
        self.assertIn("s", recorded.lower(), f"S missing: {recorded}")
        print(f"[Test 5] Verified key readout: {recorded!r}")

    def test_06_named_navigation_key_is_recognised(self):
        """A navigation key from the vocabulary reaches the application."""
        self._assert_foreground()
        res = self.client.press_key("end")
        self.assertEqual(res.get("status"), "key_sent")
        time.sleep(0.3)
        recorded = self._readout("lbl_keys")
        self.assertIn("end", recorded.lower(), f"End key not recorded: {recorded}")
        print(f"\n[Test 6] Verified key readout: {recorded!r}")

    def test_07_unrecognised_key_name_is_refused_before_any_input(self):
        """A typo is rejected immediately rather than half-executing."""
        self._assert_foreground()
        before = self._readout("lbl_keys")

        with self.assertRaises(WcuError) as ctx:
            self.client.press_key("ctrl+shift+notakey")

        self.assertEqual(ctx.exception.code, "unknown_key")
        print(f"\n[Test 7] Correctly refused unknown key: {ctx.exception.message}")

        # Invariant: no input was dispatched.
        time.sleep(0.2)
        self.assertEqual(self._readout("lbl_keys"), before, "Refused chord still sent input")

    # ------------------------------------------------------------------
    # 4. Scrolling
    # ------------------------------------------------------------------

    def test_08_targeted_vertical_scroll_changes_the_scroll_offset(self):
        """Positive gate: a scroll is verified by a changed, non-zero scroll offset."""
        meta = self._observe()
        self.assertEqual(self._scroll_offset(), 0, "Offset should start at 0")

        target = self._frame_target("pnl_scroll", meta)
        res = self.client.scroll(
            notches_y=-5,
            observation_id=meta["observation_id"],
            max_age_ms=2000,
            target=self.client.target(bbox_frame_px=target),
        )
        self.assertEqual(res.get("status"), "scrolled")
        self.assertEqual(res.get("explicit_target"), True)
        self.assertEqual(res.get("hit_test_applied"), True)
        self.assertEqual(res.get("pointer_moved"), True)
        self.assertEqual(
            res.get("events_injected"), 6,
            "Expected the pointer move plus one wheel event per notch",
        )
        print(f"\n[Test 8] scroll dispatched: {res}")

        time.sleep(0.5)
        recorded = self._readout("lbl_scroll")
        self.assertNotEqual(
            self._scroll_offset(), 0,
            f"Scroll offset did not change: {recorded}; wheel readout: {self._readout('lbl_wheel')!r}",
        )
        print(f"[Test 8] Verified scroll readout: {recorded!r}")

    def test_09_horizontal_scroll_changes_the_scroll_offset(self):
        """A wide region is reachable with a horizontal scroll, both ways."""
        meta = self._observe()
        self.assertEqual(self._scroll_offset("h"), 0, "Should start at the left edge")

        target = self._frame_target("pnl_scroll", meta)

        # Right: a positive horizontal delta scrolls the content right.
        res = self.client.scroll(
            notches_x=4,
            observation_id=meta["observation_id"],
            max_age_ms=2000,
            target=self.client.target(bbox_frame_px=target),
        )
        self.assertEqual(res.get("status"), "scrolled")
        self.assertEqual(res.get("events_injected"), 5, "Pointer move plus four notches")
        print(f"\n[Test 9] horizontal scroll right dispatched: {res}")

        time.sleep(0.5)
        right = self._scroll_offset("h")
        self.assertGreater(
            right, 0,
            f"Horizontal offset did not increase: {self._readout('lbl_scroll')!r}; "
            f"wheel readout {self._readout('lbl_wheel')!r}",
        )
        self.assertIn("axis=h", self._readout("lbl_wheel"))
        print(f"[Test 9] Verified rightward scroll: h={right}")

        # And back left, so the direction is not just "it moved".
        meta2 = self._observe()
        res2 = self.client.scroll(
            notches_x=-2,
            observation_id=meta2["observation_id"],
            max_age_ms=2000,
            target=self.client.target(bbox_frame_px=target),
        )
        self.assertEqual(res2.get("status"), "scrolled")
        time.sleep(0.5)
        back = self._scroll_offset("h")
        self.assertLess(back, right, f"Offset did not decrease: {back} !< {right}")
        self.assertGreaterEqual(back, 0)
        print(f"[Test 9] Verified leftward scroll: h={right} -> {back}")

    def test_10_untargeted_scroll_reports_that_ownership_was_not_checked(self):
        """A scroll with no explicit target is sent at the current pointer position."""
        meta = self._observe()
        # Park the pointer over the scroll region first so the wheel has an effect.
        target = self._frame_target("pnl_scroll", meta)
        self.client.hover(
            observation_id=meta["observation_id"],
            target=self.client.target(bbox_frame_px=target),
            max_age_ms=2000,
        )

        meta2 = self._observe()
        res = self.client.scroll(notches_y=-3, observation_id=meta2["observation_id"], max_age_ms=2000)
        self.assertEqual(res.get("status"), "scrolled")
        self.assertEqual(res.get("explicit_target"), False)
        self.assertEqual(res.get("hit_test_applied"), False)
        print(f"\n[Test 10] untargeted scroll dispatched: {res}")

        time.sleep(0.5)
        self.assertNotEqual(self._scroll_offset(), 0)

    def test_11_scroll_notches_are_bounded_and_the_bound_is_reported(self):
        """A long drag cannot spin indefinitely; the clamp is reported."""
        meta = self._observe()
        target = self._frame_target("pnl_scroll", meta)
        res = self.client.scroll(
            notches_y=5000,
            observation_id=meta["observation_id"],
            max_age_ms=2000,
            dry_run=True,
            target=self.client.target(bbox_frame_px=target),
        )
        self.assertEqual(res.get("status"), "dry_run")
        self.assertEqual(res.get("clamped"), True)
        self.assertEqual(res.get("notches_y"), res.get("limit"))
        print(f"\n[Test 11] Scroll bound enforced and reported: {res}")

    # ------------------------------------------------------------------
    # 5. Hover
    # ------------------------------------------------------------------

    def test_12_hover_reveals_a_hover_only_surface(self):
        """Positive gate: a hover is verified by the hover readout changing."""
        meta = self._observe()

        # Drive the pointer well clear first. Its starting position is whatever
        # the physical cursor happens to be, so the transition is what matters,
        # not the initial state.
        away = self._frame_target("lbl_keys", meta)
        self.client.hover(
            observation_id=meta["observation_id"],
            target=self.client.target(bbox_frame_px=away),
            max_age_ms=2000,
        )
        time.sleep(0.3)
        self.assertNotEqual(
            self._readout("lbl_hover"), "Hover: entered",
            "The pointer was not moved clear of the hover surface",
        )

        meta2 = self._observe()
        target = self._frame_target("pnl_hover", meta2)
        res = self.client.hover(
            observation_id=meta2["observation_id"],
            target=self.client.target(bbox_frame_px=target),
            max_age_ms=2000,
        )
        self.assertEqual(res.get("status"), "hovered")
        self.assertEqual(res.get("events_injected"), 1, "A hover changes no button state")
        print(f"\n[Test 12] hover dispatched: {res}")

        time.sleep(0.4)
        self.assertEqual(self._readout("lbl_hover"), "Hover: entered")
        print(f"[Test 12] Verified hover readout: {self._readout('lbl_hover')!r}")

        # And leaving it again is reported too.
        self.client.hover(
            observation_id=meta2["observation_id"],
            target=self.client.target(bbox_frame_px=self._frame_target("lbl_typed", meta2)),
            max_age_ms=2000,
        )
        time.sleep(0.3)
        self.assertEqual(self._readout("lbl_hover"), "Hover: left")
        print(f"[Test 12] Verified leave readout: {self._readout('lbl_hover')!r}")

    def test_13_timed_hover_interpolates_the_movement(self):
        """A hover can take a moment to travel, for applications tracking motion."""
        meta = self._observe()
        self.client.hover(
            observation_id=meta["observation_id"],
            target=self.client.target(bbox_frame_px=self._frame_target("lbl_typed", meta)),
            max_age_ms=2000,
        )
        meta2 = self._observe()
        res = self.client.hover(
            observation_id=meta2["observation_id"],
            target=self.client.target(bbox_frame_px=self._frame_target("pnl_hover", meta2)),
            max_age_ms=2000,
            duration_ms=120,
        )
        self.assertEqual(res.get("status"), "hovered")
        self.assertGreater(res.get("moves"), 1, "A timed hover should interpolate")
        self.assertEqual(res.get("events_injected"), res.get("moves"))
        print(f"\n[Test 13] Timed hover dispatched: {res}")

        time.sleep(0.4)
        self.assertEqual(self._readout("lbl_hover"), "Hover: entered")

    # ------------------------------------------------------------------
    # 6. Drag
    # ------------------------------------------------------------------

    def test_14_drag_moves_the_element_and_is_continuous(self):
        """Positive gate: a drag is verified by the recorded drop position.

        A non-zero move count also proves the gesture was continuous rather than
        a press and a release at two unrelated points.
        """
        meta = self._observe()
        start = self._frame_target("pnl_dragbox", meta)
        # Drop near the right-hand side of the arena, still inside the frame.
        arena = self._frame_target("pnl_arena", meta)
        end = [
            arena[0] + arena[2] - 150,
            arena[1] + arena[3] - 130,
            40,
            40,
        ]

        res = self.client.drag(
            from_target=self.client.target(bbox_frame_px=start),
            to_target=self.client.target(bbox_frame_px=end),
            observation_id=meta["observation_id"],
            max_age_ms=2000,
            steps=24,
            duration_ms=200,
        )
        self.assertEqual(res.get("status"), "dragged")
        self.assertEqual(res.get("steps"), 24)
        print(f"\n[Test 14] drag dispatched: {res}")

        time.sleep(0.5)
        recorded = self._readout("lbl_drop")
        self.assertTrue(recorded.startswith("Drop: "), f"No drop recorded: {recorded}")
        parts = recorded.replace("Drop: ", "").split(" ")
        self.assertEqual(len(parts), 3, f"Unexpected drop readout: {recorded}")
        drop_x, drop_y, moves = int(parts[0].rstrip(",")), int(parts[1]), int(parts[2].split("=")[1])
        self.assertNotEqual(drop_x, 0, f"Element did not move: {recorded}")
        self.assertGreater(moves, 0, f"Drag was not continuous: {recorded}")
        print(f"[Test 14] Verified drop readout: {recorded!r} (drop at {drop_x},{drop_y})")

    def test_15_drag_steps_are_bounded(self):
        """A long drag is bounded so a bad request cannot spin indefinitely."""
        meta = self._observe()
        start = self._frame_target("pnl_dragbox", meta)
        end = self._frame_target("pnl_hover", meta)
        with self.assertRaises(WcuError) as ctx:
            self.client.drag(
                from_target=self.client.target(bbox_frame_px=start),
                to_target=self.client.target(bbox_frame_px=end),
                observation_id=meta["observation_id"],
                steps=100000,
            )
        self.assertEqual(ctx.exception.code, "invalid_request")
        print(f"\n[Test 15] Correctly refused unbounded drag: {ctx.exception.message}")

    # ------------------------------------------------------------------
    # 7. Click variants
    # ------------------------------------------------------------------

    def test_16_right_click_opens_the_application_context_menu_path(self):
        """Positive gate: a right click is verified by its own readout."""
        meta = self._observe()
        self.assertEqual(self._readout("lbl_rightclick"), "RightClick: 0")

        target = self._frame_target("pnl_click", meta)
        res = self.client.click(
            observation_id=meta["observation_id"],
            target_bbox_frame_px=target,
            max_age_ms=2000,
            button="right",
        )
        self.assertEqual(res.get("status"), "clicked")
        self.assertEqual(res.get("button"), "right")
        self.assertEqual(res.get("events_injected"), 3)
        print(f"\n[Test 16] right click dispatched: {res}")

        time.sleep(0.4)
        self.assertEqual(self._readout("lbl_rightclick"), "RightClick: 1")
        print(f"[Test 16] Verified readout: {self._readout('lbl_rightclick')!r}")

    def test_17_double_click_is_recognised_by_the_application(self):
        """Positive gate: a double click is verified by its own readout."""
        meta = self._observe()
        self.assertEqual(self._readout("lbl_doubleclick"), "DoubleClick: 0")

        target = self._frame_target("pnl_click", meta)
        res = self.client.click(
            observation_id=meta["observation_id"],
            target_bbox_frame_px=target,
            max_age_ms=2000,
            click_count=2,
        )
        self.assertEqual(res.get("status"), "clicked")
        self.assertEqual(res.get("click_count"), 2)
        self.assertEqual(res.get("events_injected"), 5)
        print(f"\n[Test 17] double click dispatched: {res}")

        time.sleep(0.4)
        self.assertEqual(self._readout("lbl_doubleclick"), "DoubleClick: 1")
        print(f"[Test 17] Verified readout: {self._readout('lbl_doubleclick')!r}")

    def test_18_point_target_and_bbox_target_reach_the_same_place(self):
        """The uniform target accepts either form, and both are hit-test owned."""
        meta = self._observe()
        target = self._frame_target("pnl_click", meta)
        centre = [target[0] + target[2] // 2, target[1] + target[3] // 2]

        res_bbox = self.client.click(
            observation_id=meta["observation_id"],
            target=self.client.target(bbox_frame_px=target),
            max_age_ms=2000,
            dry_run=True,
        )
        res_point = self.client.click(
            observation_id=meta["observation_id"],
            target=self.client.target(point_frame_px=centre),
            max_age_ms=2000,
            dry_run=True,
        )
        self.assertEqual(res_bbox["screen_x"], res_point["screen_x"])
        self.assertEqual(res_bbox["screen_y"], res_point["screen_y"])
        print(
            f"\n[Test 18] bbox centre and explicit point agree: "
            f"({res_bbox['screen_x']}, {res_bbox['screen_y']})"
        )

    # ------------------------------------------------------------------
    # 8. Shared refusals for typing, chords and scrolling
    # ------------------------------------------------------------------

    def test_19_typing_refused_after_the_window_moves(self):
        """The most common real failure: the window moved between look and act."""
        meta = self._observe()
        before = self._field_text()
        hwnd_int = int(self.action_window["hwnd"])

        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd_int, ctypes.byref(rect))
        w, h = rect.right - rect.left, rect.bottom - rect.top
        user32.MoveWindow(hwnd_int, rect.left + 40, rect.top + 30, w, h, True)
        user32.SetForegroundWindow(hwnd_int)
        time.sleep(0.3)

        try:
            with self.assertRaises(WcuError) as ctx:
                self.client.type_text(
                    "SHOULD-NOT-LAND",
                    observation_id=meta["observation_id"],
                    max_age_ms=3000,
                )
            self.assertIn(ctx.exception.code, ["geometry_changed", "foreground_changed"])
            print(f"\n[Test 19] Correctly refused typing on moved window: {ctx.exception.message}")
        finally:
            user32.MoveWindow(hwnd_int, rect.left, rect.top, w, h, True)
            user32.SetForegroundWindow(hwnd_int)
            time.sleep(0.3)

        # Invariant: the application's state is untouched.
        self.assertEqual(self._field_text(), before, "A refused typing still reached the field")
        print(f"[Test 19] Invariant held, field unchanged: {before!r}")

    def test_20_chord_refused_after_the_window_moves(self):
        """A chord decision made against a moved window is refused."""
        meta = self._observe()
        before = self._readout("lbl_keys")
        hwnd_int = int(self.action_window["hwnd"])

        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd_int, ctypes.byref(rect))
        w, h = rect.right - rect.left, rect.bottom - rect.top
        user32.MoveWindow(hwnd_int, rect.left + 40, rect.top + 30, w, h, True)
        user32.SetForegroundWindow(hwnd_int)
        time.sleep(0.3)

        try:
            with self.assertRaises(WcuError) as ctx:
                self.client.press_key(
                    "ctrl+shift+s", observation_id=meta["observation_id"], max_age_ms=3000
                )
            self.assertIn(ctx.exception.code, ["geometry_changed", "foreground_changed"])
            print(f"\n[Test 20] Correctly refused chord on moved window: {ctx.exception.message}")
        finally:
            user32.MoveWindow(hwnd_int, rect.left, rect.top, w, h, True)
            user32.SetForegroundWindow(hwnd_int)
            time.sleep(0.3)

        self.assertEqual(self._readout("lbl_keys"), before, "A refused chord still reached the app")

    def test_21_scroll_refused_after_the_window_moves(self):
        """A scroll planned against a moved window is refused."""
        meta = self._observe()
        before = self._readout("lbl_scroll")
        target = self._frame_target("pnl_scroll", meta)
        hwnd_int = int(self.action_window["hwnd"])

        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd_int, ctypes.byref(rect))
        w, h = rect.right - rect.left, rect.bottom - rect.top
        user32.MoveWindow(hwnd_int, rect.left + 40, rect.top + 30, w, h, True)
        user32.SetForegroundWindow(hwnd_int)
        time.sleep(0.3)

        try:
            with self.assertRaises(WcuError) as ctx:
                self.client.scroll(
                    notches_y=-5,
                    observation_id=meta["observation_id"],
                    max_age_ms=3000,
                    target=self.client.target(bbox_frame_px=target),
                )
            self.assertIn(ctx.exception.code, ["geometry_changed", "foreground_changed"])
            print(f"\n[Test 21] Correctly refused scroll on moved window: {ctx.exception.message}")
        finally:
            user32.MoveWindow(hwnd_int, rect.left, rect.top, w, h, True)
            user32.SetForegroundWindow(hwnd_int)
            time.sleep(0.3)

        self.assertEqual(self._readout("lbl_scroll"), before, "A refused scroll still reached the app")

    def test_22_shared_refusals_for_keyboard_and_scroll(self):
        """Expired and mismatched observations, and an out-of-frame target."""
        self._assert_foreground()

        # Expired observation.
        meta = self._observe()
        time.sleep(0.15)
        with self.assertRaises(WcuError) as ctx:
            self.client.type_text("nope", observation_id=meta["observation_id"], max_age_ms=10)
        self.assertEqual(ctx.exception.code, "stale_observation")

        # Mismatched observation.
        with self.assertRaises(WcuError) as ctx:
            self.client.press_key("ctrl+s", observation_id=987654321, max_age_ms=2000)
        self.assertEqual(ctx.exception.code, "stale_observation")
        print(f"\n[Test 22] Correctly refused expired and mismatched observations")

        # A target outside the observed frame.
        meta2 = self._observe()
        w, h = meta2["width"], meta2["height"]
        with self.assertRaises(WcuError) as ctx:
            self.client.scroll(
                notches_y=-3,
                observation_id=meta2["observation_id"],
                max_age_ms=2000,
                target=self.client.target(bbox_frame_px=[w + 400, h + 400, 60, 60]),
            )
        self.assertEqual(ctx.exception.code, "invalid_coordinates")
        print(f"[Test 22] Correctly refused out-of-frame target: {ctx.exception.message}")

        # A drag endpoint outside the frame.
        meta3 = self._observe()
        inside = self._frame_target("pnl_hover", meta3)
        with self.assertRaises(WcuError) as ctx:
            self.client.drag(
                from_target=self.client.target(bbox_frame_px=inside),
                to_target=self.client.target(bbox_frame_px=[w + 400, h + 400, 60, 60]),
                observation_id=meta3["observation_id"],
            )
        self.assertEqual(ctx.exception.code, "invalid_coordinates")
        print(f"[Test 22] Correctly refused drag endpoint outside the frame")

    def test_23_hover_and_drag_refused_when_the_target_is_covered(self):
        """A window in front of the target does not receive the input."""
        self._assert_foreground()
        meta = self._observe()
        hover_target = self._frame_target("pnl_hover", meta)
        drag_from = self._frame_target("pnl_dragbox", meta)
        before_hover = self._readout("lbl_hover")
        before_drop = self._readout("lbl_drop")

        # Ask the engine where the surface physically is. It is per-monitor DPI
        # aware, so its answer is in physical pixels regardless of this
        # process's own coordinate space.
        probe = self.client.click(
            observation_id=meta["observation_id"],
            target_bbox_frame_px=hover_target,
            max_age_ms=2000,
            dry_run=True,
        )
        point_x, point_y = probe["screen_x"], probe["screen_y"]
        self.assertEqual(
            probe.get("status"), "dry_run",
            "The surface should be reachable before anything covers it",
        )

        # Cover it with a topmost window owned by this process. It is shown
        # without activating, so the fixture keeps the foreground and any refusal
        # is attributable to occlusion rather than to a foreground change.
        el = self._by_id("pnl_hover")
        cover = _CoverWindow(
            *self._to_local(
                el["bounds"]["x"] - 30, el["bounds"]["y"] - 30,
                el["bounds"]["w"] + 60, el["bounds"]["h"] + 60,
            )
        )
        cover.start()
        self.addCleanup(cover.stop)
        time.sleep(0.4)
        self.assertEqual(
            user32.GetForegroundWindow(), int(self.action_window["hwnd"]),
            "Showing the cover must not have taken the foreground",
        )

        # Coverage is confirmed through the engine, which is the authority here:
        # it reports the window that owns the physical point, and the point it
        # used is the one it just told us about.
        with self.assertRaises(WcuError) as probe_ctx:
            self.client.click(
                observation_id=meta["observation_id"],
                target_bbox_frame_px=hover_target,
                max_age_ms=2000,
                dry_run=True,
            )
        self.assertEqual(
            probe_ctx.exception.code, "target_occluded",
            "The cover window is not actually over the hover surface, so this "
            f"test would not be testing occlusion at all: {probe_ctx.exception.message}",
        )
        occluder = _occluding_root(probe_ctx.exception.message)
        self.assertEqual(
            occluder, cover.hwnd,
            "The point is occluded, but not by the cover window this test placed",
        )
        print(f"\n[Test 23] Point ({point_x}, {point_y}) is now owned by the cover")

        try:
            with self.assertRaises(WcuError) as ctx:
                self.client.hover(
                    observation_id=meta["observation_id"],
                    target=self.client.target(bbox_frame_px=hover_target),
                    max_age_ms=3000,
                )
            self.assertEqual(ctx.exception.code, "target_occluded")
            print(f"[Test 23] Correctly refused hover on covered target: {ctx.exception.message}")

            with self.assertRaises(WcuError) as ctx:
                self.client.drag(
                    from_target=self.client.target(bbox_frame_px=drag_from),
                    to_target=self.client.target(bbox_frame_px=hover_target),
                    observation_id=meta["observation_id"],
                    max_age_ms=3000,
                )
            self.assertEqual(ctx.exception.code, "target_occluded")
            print(f"[Test 23] Correctly refused drag onto covered target: {ctx.exception.message}")
        finally:
            cover.stop()
            self._assert_foreground()
            time.sleep(0.3)

        self.assertEqual(self._readout("lbl_hover"), before_hover, "A refused hover still landed")
        self.assertEqual(self._readout("lbl_drop"), before_drop, "A refused drag still landed")
        print("[Test 23] Invariants held, application state untouched")

    def test_24_typing_bounded(self):
        """A very long string fails cleanly rather than injecting unbounded input."""
        self._assert_foreground()
        before = self._field_text()

        # Comfortably past the engine's own 4096 character bound, but small
        # enough to fit the protocol header so the engine's limit is what refuses.
        with self.assertRaises(WcuError) as ctx:
            self.client.type_text("x" * 5000, timeout_sec=20.0)
        self.assertEqual(ctx.exception.code, "invalid_request")
        print(f"\n[Test 24] Correctly refused oversized text: {ctx.exception.message}")
        self.assertEqual(self._field_text(), before, "A refused typing still reached the field")

        # Beyond the protocol header limit the client refuses before sending.
        with self.assertRaises(ValueError):
            self.client.type_text("y" * 200000, timeout_sec=20.0)
        print("[Test 24] Correctly refused a request larger than the protocol header")
        self.assertEqual(self._field_text(), before)

    # ------------------------------------------------------------------
    # 2b. Committed-text insertion (runs after test_27 by name order)
    # ------------------------------------------------------------------

    def _clear_field(self) -> None:
        """Empty the typing target through UIA (setup, not the dispatch under test)."""
        el = self._by_id("txt_target")
        self.client.uia_action(token=el["token"], action="set_value", value="")
        self._wait_until(
            lambda: self._field_text() == "", "Field did not clear"
        )

    def _focus_field(self) -> None:
        """Put the caret in the typing target, so insertion has somewhere to go."""
        meta = self._observe()
        field = self._frame_target("txt_target", meta)
        click_res = self.client.click(
            observation_id=meta["observation_id"],
            target_bbox_frame_px=field,
            max_age_ms=2000,
        )
        self.assertEqual(click_res.get("status"), "clicked")
        time.sleep(0.2)

    def test_28_commit_inserts_text_without_touching_the_clipboard(self):
        """A single edit message lands byte-perfect with no input events."""
        self._clear_field()
        self._focus_field()

        text = "Hello Commit 42"
        res = self.client.type_text(text, method="commit", timeout_sec=20.0)
        self.assertEqual(res.get("status"), "typed")
        self.assertEqual(res.get("method"), "commit")
        self.assertEqual(res.get("messages_sent"), 1)
        self.assertEqual(res.get("events_injected"), 0)
        self.assertEqual(res.get("verification"), "matched")
        self.assertEqual(res.get("clipboard"), "unchanged")
        print(f"\n[Test 28] type_text commit dispatched: {res}")

        time.sleep(0.4)
        self.assertEqual(self._field_text(), text, "The field did not receive the committed text")

    def test_29_commit_handles_unicode(self):
        """Accented and non-Latin text arrives intact through the message path."""
        self._clear_field()
        self._focus_field()

        text = "café 日本語 ✓"
        res = self.client.type_text(text, method="commit", timeout_sec=20.0)
        self.assertEqual(res.get("status"), "typed")
        self.assertEqual(res.get("verification"), "matched")
        print(f"\n[Test 29] type_text commit dispatched: {res}")

        time.sleep(0.4)
        self.assertEqual(self._field_text(), text, "Unicode committed text was mangled")

    def test_30_commit_inserts_at_the_caret_in_a_nonempty_field(self):
        """With TextPattern the selection is known, so mid-document insertion is
        verified exactly, not just appended."""
        el = self._by_id("txt_target")
        self.client.uia_action(token=el["token"], action="set_value", value="seed 123")
        self._wait_until(lambda: self._field_text() == "seed 123", "Field did not seed")
        self._focus_field()
        self.client.press_key("end")
        time.sleep(0.2)

        res = self.client.type_text("!", method="commit", timeout_sec=20.0)
        self.assertEqual(res.get("status"), "typed")
        self.assertEqual(res.get("verification"), "matched")
        print(f"\n[Test 30] type_text commit dispatched: {res}")

        time.sleep(0.4)
        self.assertEqual(self._field_text(), "seed 123!", "Commit did not land at the caret")
        self._clear_field()

    # ------------------------------------------------------------------
    # 9. Window focus and switching
    # ------------------------------------------------------------------

    def test_25_focus_reports_the_new_and_the_previous_foreground(self):
        """A focus change is an explicit, verifiable event."""
        self._assert_foreground()
        res = self.client.focus_window(
            self.action_window["hwnd"],
            pid=self.action_window["pid"],
            process_create_time_utc=self.action_window["process_create_time_utc"],
        )
        self.assertEqual(res.get("status"), "focused")
        self.assertEqual(res.get("verified"), True)
        self.assertIn("previous_foreground", res)
        self.assertIn("foreground", res)
        self.assertEqual(res.get("observation_cleared"), True)
        self.assertEqual(
            res["foreground"]["hwnd"],
            str(self.action_window["hwnd"]),
            "Verification should be against the actual foreground window",
        )
        print(f"\n[Test 25] focus_window verified: {res}")

    def test_26_switching_invalidates_the_previous_windows_observation(self):
        """A target chosen before switching is never usable after it."""
        # Attach to, observe, and act on the primary window.
        self._attach_and_foreground(self.primary_window)
        meta, _ = self.client.observe()
        old_obs_id = meta["observation_id"]
        old_counter = self._read_primary_counter()
        print(f"\n[Test 26] Primary window observed, counter={old_counter}, obs={old_obs_id}")

        # Switch to the action window.
        res = self.client.switch_window(
            self.action_window["hwnd"],
            pid=self.action_window["pid"],
            process_create_time_utc=self.action_window["process_create_time_utc"],
        )
        self.assertEqual(res["focus"]["status"], "focused")
        self.assertEqual(
            res["focus"]["foreground"]["hwnd"], str(self.action_window["hwnd"])
        )
        print(f"[Test 26] Switched; new observation {res['observation_id']}")
        self.assertNotEqual(res["observation_id"], old_obs_id)

        # The previous window's observation is no longer usable.
        with self.assertRaises(WcuError) as ctx:
            self.client.click(
                observation_id=old_obs_id,
                target_bbox_frame_px=[100, 100, 40, 40],
                max_age_ms=5000,
            )
        self.assertEqual(ctx.exception.code, "stale_observation")
        print(f"[Test 26] Correctly refused the previous window's observation: {ctx.exception.message}")

        # Acting on the new window works.
        meta2 = self._observe()
        target = self._frame_target("pnl_click", meta2)
        clicked = self.client.click(
            observation_id=meta2["observation_id"],
            target_bbox_frame_px=target,
            max_age_ms=2000,
            button="right",
        )
        self.assertEqual(clicked.get("status"), "clicked")
        time.sleep(0.4)
        self.assertEqual(self._readout("lbl_rightclick"), "RightClick: 1")
        print(f"[Test 26] Acted successfully on the newly attached window")

        # Invariant: the previous window was not affected.
        self._attach_and_foreground(self.primary_window)
        self.assertEqual(self._read_primary_counter(), old_counter)
        print(f"[Test 26] Invariant held, primary window counter still {old_counter}")

    def _read_primary_counter(self) -> int:
        elements, _ = self.client.inspect(max_depth=6, max_elements=50)
        el = next((e for e in elements if e.get("automation_id") == "lbl_counter"), None)
        self.assertIsNotNone(el, "lbl_counter not found on the primary fixture window")
        return int(el.get("name", "").split(":")[1].strip())

    def test_27_refused_focus_names_the_window_that_holds_the_foreground(self):
        """A refused focus is reported honestly against the real foreground.

        The precondition is a genuine Windows policy rather than a contrivance:
        the process that owns the foreground calls
        ``LockSetForegroundWindow(LSFW_LOCK)``, after which no other process may
        take it.

        Two earlier designs were wrong, and the reasons are worth recording:

        * A window on a private desktop is rejected as ``window_gone`` during the
          engine's window lookup, before any focus is attempted. That tests
          lookup, not refusal.
        * A separate process asked to activate its own window and then lock it
          never succeeded. ``SetForegroundWindow`` has eligibility conditions
          that a freshly launched process does not meet, and being a child of the
          test runner is not among them.

        So the lock is taken by the window that already legitimately holds the
        foreground, and the engine is asked to focus a *different* window, which
        is what makes the refusal meaningful.

        One further condition has to be handled, and it is not obvious. A
        foreground lock blocks a foreground request, but it does not remove the
        requester's eligibility to make one, and a process that injected input
        recently is eligible because it was the last input receiver. The engine
        injects input throughout this suite, so a lock alone was not enough: the
        engine could still take the foreground, which would drop the holder's
        activation and release the lock. The fixture therefore sends a
        zero-distance mouse movement of its own immediately before locking, making
        itself rather than the engine the most recent input receiver. It has to be
        the fixture that does this: input from the test runner would make the
        runner eligible and invalidate the SetForegroundWindow precheck below.
        """
        target = self.primary_window
        self._attach_and_foreground(self.action_window)
        action_hwnd = int(self.action_window["hwnd"])
        self.assertEqual(
            user32.GetForegroundWindow(), action_hwnd,
            "The action fixture must own the foreground before it can lock it",
        )

        # Baseline. The engine must be able to focus the target under normal
        # conditions, otherwise a later refusal proves nothing: it could be
        # refusing for any unrelated reason.
        focused = self.client.focus_window(
            target["hwnd"],
            pid=target["pid"],
            process_create_time_utc=target["process_create_time_utc"],
        )
        self.assertEqual(focused.get("status"), "focused")
        self.assertEqual(
            user32.GetForegroundWindow(), int(target["hwnd"]),
            "Baseline failed: the engine could not focus the target before any lock",
        )
        print(f"\n[Test 27] Baseline established: the engine can focus {target['hwnd']}")

        # Put the action window back in front and have it lock the foreground.
        # The engine does the focusing, not the test process: after the baseline
        # the test process is no longer eligible to take the foreground itself,
        # which is the same eligibility rule that makes this test possible. Using
        # the engine here is precondition setup, well before the assertion under
        # test and while the foreground is still unlocked.
        self.client.attach(
            self.action_window["hwnd"],
            self.action_window["pid"],
            self.action_window["process_create_time_utc"],
        )
        self.client.focus_window(
            self.action_window["hwnd"],
            pid=self.action_window["pid"],
            process_create_time_utc=self.action_window["process_create_time_utc"],
        )
        self.assertEqual(
            user32.GetForegroundWindow(), action_hwnd,
            "Could not return the action window to the foreground before locking",
        )
        locked = self._request_action_focus_lock(lock=True)
        self.assertTrue(
            locked,
            "The action fixture could not lock the foreground it owns, so a "
            "refusal would not be attributable to the lock",
        )
        self.assertEqual(user32.GetForegroundWindow(), action_hwnd)
        print(f"[Test 27] Action fixture {action_hwnd} holds and has locked the foreground")

        try:
            # The lock is in force: an ordinary SetForegroundWindow cannot move
            # the foreground. Verified before the engine is asked, so that a
            # refusal is known to be caused by the lock and not by chance.
            user32.SetForegroundWindow(ctypes.c_void_p(int(target["hwnd"])))
            time.sleep(0.5)
            self.assertNotEqual(
                user32.GetForegroundWindow(), int(target["hwnd"]),
                "The foreground lock is not in effect, so this test would not be "
                "testing focus refusal at all",
            )
            self.assertEqual(
                user32.GetForegroundWindow(), action_hwnd,
                "The holder lost the foreground, which releases the lock and "
                "would invalidate this test",
            )
            print("[Test 27] Verified the lock blocks an ordinary SetForegroundWindow")

            with self.assertRaises(WcuError) as ctx:
                self.client.focus_window(
                    target["hwnd"],
                    pid=target["pid"],
                    process_create_time_utc=target["process_create_time_utc"],
                )

            self.assertEqual(
                ctx.exception.code, "focus_refused",
                f"Expected an honest refusal, got {ctx.exception.code}: {ctx.exception.message}",
            )
            details = ctx.exception.details or {}
            reported = details.get("actual_foreground")
            self.assertIsNotNone(reported, "A refused focus must name the actual foreground")
            self.assertNotEqual(
                int(reported["hwnd"]), int(target["hwnd"]),
                "The refusal must not name the window that failed to take focus",
            )
            # The named holder is the one that really holds it, not engine state.
            self.assertEqual(
                int(reported["hwnd"]), action_hwnd,
                "The refusal named a window that does not actually hold the foreground",
            )
            self.assertEqual(
                user32.GetForegroundWindow(), action_hwnd,
                "A refused focus must not take the foreground",
            )
            print(f"[Test 27] Refused focus reported honestly: {ctx.exception.message}")
            print(f"[Test 27] Actual foreground named: hwnd={reported['hwnd']} "
                  f"title={reported.get('title')!r}")
        finally:
            # Released explicitly, because a lock left in place would stop every
            # later test from taking the foreground at all. Forcing the fixture
            # process to exit would not do this reliably, since termination does
            # not run managed shutdown handlers.
            self._request_action_focus_lock(lock=False)
            self._wait_until(
                lambda: user32.GetForegroundWindow() == action_hwnd,
                "Unlocking did not leave the action window in the foreground",
            )
            self._assert_foreground()
            time.sleep(0.3)

        print("[Test 27] Invariant held, foreground released and back on the action window")

    def _request_action_focus_lock(self, lock: bool) -> bool:
        """Ask the action fixture to lock or unlock the foreground it owns.

        Sent as a window message so the work happens on the fixture's own UI
        thread, which is the only thread entitled to lock its own foreground.
        Returns the fixture's own report of whether it succeeded.
        """
        hwnd = int(self.action_window["hwnd"])
        message = WM_APP_LOCK_FOREGROUND if lock else WM_APP_UNLOCK_FOREGROUND
        user32.SendMessageW.restype = ctypes.c_ssize_t
        user32.SendMessageW.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t,
        ]
        result = user32.SendMessageW(ctypes.c_void_p(hwnd), message, 0, 0)
        if not result:
            print(
                f"[Test 27] Fixture reported it could not "
                f"{'lock' if lock else 'unlock'}"
            )
        return bool(result)


if __name__ == "__main__":
    unittest.main()
