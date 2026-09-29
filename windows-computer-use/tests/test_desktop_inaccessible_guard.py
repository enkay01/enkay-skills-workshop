"""Desktop-Inaccessibility Guard Suite.

Covers the one guard check that had no test at all: the refusal when the session
has no usable interactive desktop, reported as ``desktop_inaccessible``.

The condition itself cannot be produced from a test on a live interactive
session, so this suite reaches it through the engine's one-way test valve. Every
refusal is asserted twice: by the error code the engine returns, and by reading
the application's own reported state afterwards to prove nothing was dispatched.
A refusal that the engine reported but still acted on would fail here.

Why the valve, and what was tried first, is recorded in
``specs/desktop-inaccessibility-guard-testing.md``. The short version is that
``OpenInputDesktop`` reports the *window station's* input desktop rather than the
calling thread's, so per-thread desktop attachment cannot change what the guard
reads; creating a non-interactive window station needs a privilege an interactive
user does not have; and locking or switching the session would destroy the desktop
the suite itself runs in.
"""

import os
import subprocess
import sys
import time
import unittest
from typing import List

import ctypes

root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(root_dir, "client"))

from wcu_client import WcuClient, WcuError  # noqa: E402

user32 = ctypes.windll.user32

# Must match TEST_DESKTOP_INACCESSIBLE_VAR in the engine.
DESKTOP_VALVE_VAR = "WCU_TEST_DESKTOP_INACCESSIBLE"

FIXTURE_EXE = os.path.abspath(
    os.path.join(
        root_dir, "tests", "fixture_app", "bin", "Debug", "net6.0-windows", "fixture_app.exe"
    )
)
ACTION_WINDOW_TITLE = "WCU_Native_Action_Fixture"

MOUSEEVENTF_MOVE = 0x0001
INPUT_MOUSE = 0


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT)]

    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_ulong), ("u", _U)]


def _claim_foreground_eligibility() -> None:
    """Make this process eligible to take the foreground.

    Windows only lets a process that injected the most recent input call
    `SetForegroundWindow`, or one started by the foreground process. When this
    suite runs first, nothing has injected any input yet, so the fixture cannot be
    brought forward and every action would be refused with `foreground_changed`
    for a reason that has nothing to do with the desktop check.

    A zero-distance relative mouse move is enough. It displaces nothing and
    presses nothing, but it makes the receiving process the last input receiver,
    which is the same technique the action fixture uses before locking the
    foreground in the focus-refusal test.
    """
    user32.SendInput.argtypes = [
        ctypes.c_ulong, ctypes.POINTER(_INPUT), ctypes.c_int
    ]
    user32.SendInput.restype = ctypes.c_ulong

    event = _INPUT()
    event.type = INPUT_MOUSE
    event.mi.dx = 0
    event.mi.dy = 0
    event.mi.dwFlags = MOUSEEVENTF_MOVE
    sent = user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(_INPUT))
    if sent != 1:
        raise RuntimeError(
            f"Could not claim foreground eligibility: SendInput sent {sent} of 1 event"
        )



class TestDesktopInaccessibleGuard(unittest.TestCase):
    """Every input-dispatching action must refuse when the desktop is unusable."""

    @classmethod
    def setUpClass(cls) -> None:
        if not os.path.exists(FIXTURE_EXE):
            raise unittest.SkipTest(f"Fixture not built at {FIXTURE_EXE}; run dotnet build")

        cls.fixture_proc = subprocess.Popen([FIXTURE_EXE, "--actions"])

        # An ordinary engine, used to observe the fixture and as the control that
        # proves the harness works when the condition is not forced.
        cls.control = WcuClient()
        cls.control.start()

        window = cls._wait_for_window(cls.control, ACTION_WINDOW_TITLE, cls.fixture_proc)
        cls.action_window = window
        cls.control.attach(
            window["hwnd"], window["pid"], window.get("process_create_time_utc", "unknown")
        )

        # A second engine whose process environment carries the valve. The engine
        # inherits this at launch, so the variable is removed again immediately;
        # the value is fixed for the lifetime of that process.
        #
        # WcuClient spawns the engine without an explicit environment, so it
        # inherits this process's. Setting it here rather than passing an `env`
        # keeps the client unchanged. The suite runs sequentially; a parallel
        # runner would need the engine launched by an explicit env instead.
        os.environ[DESKTOP_VALVE_VAR] = "1"
        try:
            cls.valved = WcuClient()
            cls.valved.start()
        finally:
            os.environ.pop(DESKTOP_VALVE_VAR, None)

        cls.valved.attach(
            window["hwnd"], window["pid"], window.get("process_create_time_utc", "unknown")
        )

        # Claimed once, here, rather than before every action: a stray mouse event
        # during a test could otherwise perturb the very state a refusal is
        # asserted against.
        _claim_foreground_eligibility()

    @classmethod
    def tearDownClass(cls) -> None:
        for client in (getattr(cls, "valved", None), getattr(cls, "control", None)):
            try:
                if client is not None:
                    client.stop()
            except Exception:
                pass
        proc = getattr(cls, "fixture_proc", None)
        try:
            if proc is not None and proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=2.0)
        except Exception:
            pass

        # Windows grants foreground eligibility to whichever process received the
        # most recent input, and a later suite's engine inherits it only while that
        # process is alive. Test 28 clicks, which hands eligibility to this class's
        # control engine; that engine is now dead, and the next suite finds no
        # eligible process and cannot bring its own fixture forward. Claiming it
        # again on the way out leaves the session as this class found it.
        _claim_foreground_eligibility()

    @classmethod
    def _wait_for_window(cls, client: WcuClient, title: str, proc: subprocess.Popen) -> dict:
        deadline = time.time() + 20.0
        last: List[str] = []
        while time.time() < deadline:
            windows = client.list_windows()
            match = next((w for w in windows if title in w.get("title", "")), None)
            if match:
                return match
            last = [w.get("title") for w in windows]
            if proc.poll() is not None:
                raise RuntimeError(f"Fixture exited {proc.returncode} before '{title}' appeared")
            time.sleep(0.25)
        raise RuntimeError(f"'{title}' did not appear; saw {last[:20]}")

    # --- fixture observation ---------------------------------------------------

    def _elements(self) -> list:
        # Read through the unconditioned engine. Observation is deliberately not
        # part of what the desktop check gates, and an independent reader is the
        # right thing to judge the fixture with anyway.
        elements, _ = self.control.inspect(max_depth=8, max_elements=200)
        return elements

    def _by_id(self, automation_id: str) -> dict:
        el = next(
            (e for e in self._elements() if e.get("automation_id") == automation_id), None
        )
        self.assertIsNotNone(el, f"{automation_id} not found in the accessibility tree")
        return el

    def _readout(self, automation_id: str) -> str:
        """Read a fixture label's text, i.e. the application's own reported state."""
        return self._by_id(automation_id).get("name", "")

    def _field_text(self, automation_id: str = "txt_target") -> str:
        return self._by_id(automation_id).get("value", "")

    def _frame_target(self, automation_id: str, obs_meta: dict) -> list:
        """Express an element's screen bounds in the observed frame's pixels."""
        b = self._by_id(automation_id)["bounds"]
        cb = obs_meta["capture_bounds_physical_px"]
        sx = obs_meta["width"] / cb["w"]
        sy = obs_meta["height"] / cb["h"]
        return [
            int((b["x"] - cb["x"]) * sx),
            int((b["y"] - cb["y"]) * sy),
            max(2, int(b["w"] * sx)),
            max(2, int(b["h"] * sy)),
        ]

    def _scroll_offset(self, axis: str = "v") -> int:
        recorded = self._readout("lbl_scroll")
        for part in recorded.split():
            if part.startswith(f"{axis}="):
                return int(part.split("=")[1])
        self.fail(f"Could not parse {axis} offset from {recorded!r}")

    def _assert_foreground(self) -> None:
        """Put the fixture in front, so a refusal is attributable to the desktop.

        The desktop check runs before the foreground check, so an action with the
        fixture behind another window would be refused either way. Bringing it
        forward removes that ambiguity: the only reason left to refuse is the
        desktop.
        """
        hwnd_int = int(self.action_window["hwnd"])
        for attempt in range(10):
            if user32.GetForegroundWindow() == hwnd_int:
                return
            if attempt == 2:
                # An earlier action in this class may have made the engine the
                # last input receiver, taking this process's eligibility with it.
                _claim_foreground_eligibility()
            user32.SetForegroundWindow(hwnd_int)
            time.sleep(0.1)
        self.assertEqual(
            user32.GetForegroundWindow(), hwnd_int, "Could not foreground the fixture"
        )

    def _valved_observation(self) -> dict:
        """Observe through the valved engine and assert the desktop is refused.

        Observation itself is not gated by the desktop check, so this succeeds.
        That is deliberate and is what lets the refusal be reached at all.
        """
        meta, _ = self.valved.observe(timeout_ms=2000)
        return meta

    def _assert_refused(self, call, label: str) -> str:
        """Assert the action is refused, and return the message for inspection."""
        with self.assertRaises(WcuError) as ctx:
            call()
        self.assertEqual(
            ctx.exception.code,
            "desktop_inaccessible",
            f"{label} should have been refused for the desktop, "
            f"got {ctx.exception.code}: {ctx.exception.message}",
        )
        return ctx.exception.message

    # --- control ---------------------------------------------------------------

    def test_28_control_actions_succeed_without_the_condition(self):
        """The same fixture and the same arguments work when nothing is forced.

        Without this, a suite in which every action refused for some unrelated
        reason would pass every other test here while proving nothing.
        """
        self._assert_foreground()
        meta, _ = self.control.observe(timeout_ms=2000)

        target = self.control.target(
            bbox_frame_px=self._frame_target("pnl_click", meta)
        )
        self.control.click(observation_id=meta["observation_id"], target=target, click_count=2)
        self.assertEqual(
            self._readout("lbl_doubleclick"),
            "DoubleClick: 1",
            "The control engine could not click, so the refusals below would prove nothing",
        )
        print("\n[Test 28] The unconditioned engine performs the action successfully")

    # --- refusals --------------------------------------------------------------

    def test_29_click_refused_and_dispatched_nothing(self):
        self._assert_foreground()
        meta = self._valved_observation()
        target = self.valved.target(bbox_frame_px=self._frame_target("pnl_click", meta))
        before = self._readout("lbl_doubleclick")

        self._assert_refused(
            lambda: self.valved.click(
                observation_id=meta["observation_id"], target=target, click_count=2
            ),
            "click",
        )
        self.assertEqual(
            self._readout("lbl_doubleclick"),
            before,
            "A refused click still reached the application",
        )
        print("[Test 29] Click refused; the application recorded no click")

    def test_30_right_click_refused_and_dispatched_nothing(self):
        self._assert_foreground()
        meta = self._valved_observation()
        target = self.valved.target(bbox_frame_px=self._frame_target("pnl_click", meta))
        before = self._readout("lbl_rightclick")

        self._assert_refused(
            lambda: self.valved.click(
                observation_id=meta["observation_id"], target=target, button="right"
            ),
            "right click",
        )
        self.assertEqual(self._readout("lbl_rightclick"), before, "A refused right click landed")
        print("[Test 30] Right click refused; the application recorded no click")

    def test_31_typing_refused_and_dispatched_nothing(self):
        self._assert_foreground()
        meta = self._valved_observation()
        before = self._field_text()
        self.assertEqual(before, "", "The fixture field should start empty")

        self._assert_refused(
            lambda: self.valved.type_text(
                "should never appear", observation_id=meta["observation_id"]
            ),
            "type_text",
        )
        self.assertEqual(
            self._field_text(), before, "A refused typing action still reached the application"
        )
        print("[Test 31] Typing refused; the field is still empty")

    def test_32_chord_refused_and_dispatched_nothing(self):
        self._assert_foreground()
        meta = self._valved_observation()
        before = self._readout("lbl_keys")

        self._assert_refused(
            lambda: self.valved.press_key("ctrl+s", observation_id=meta["observation_id"]),
            "press_key",
        )
        self.assertEqual(
            self._readout("lbl_keys"), before, "A refused chord still reached the application"
        )
        print("[Test 32] Key chord refused; the application recorded no key")

    def test_33_scroll_refused_and_dispatched_nothing(self):
        self._assert_foreground()
        meta = self._valved_observation()
        target = self.valved.target(
            bbox_frame_px=self._frame_target("pnl_scroll", meta)
        )
        before_v, before_h = self._scroll_offset("v"), self._scroll_offset("h")

        self._assert_refused(
            lambda: self.valved.scroll(
                notches_y=-4, observation_id=meta["observation_id"], target=target
            ),
            "scroll",
        )
        self.assertEqual(
            (self._scroll_offset("v"), self._scroll_offset("h")),
            (before_v, before_h),
            "A refused scroll still moved the target",
        )
        print("[Test 33] Scroll refused; both offsets unchanged")

    def test_34_hover_refused_and_dispatched_nothing(self):
        self._assert_foreground()
        meta = self._valved_observation()
        target = self.valved.target(bbox_frame_px=self._frame_target("pnl_hover", meta))
        before = self._readout("lbl_hover")

        self._assert_refused(
            lambda: self.valved.hover(observation_id=meta["observation_id"], target=target),
            "hover",
        )
        self.assertEqual(self._readout("lbl_hover"), before, "A refused hover still landed")
        print("[Test 34] Hover refused; the application recorded no hover")

    def test_35_drag_refused_and_dispatched_nothing(self):
        self._assert_foreground()
        meta = self._valved_observation()
        from_target = self.valved.target(
            bbox_frame_px=self._frame_target("pnl_dragbox", meta)
        )
        to_target = self.valved.target(bbox_frame_px=self._frame_target("pnl_hover", meta))
        before = self._readout("lbl_drop")

        self._assert_refused(
            lambda: self.valved.drag(
                from_target=from_target,
                to_target=to_target,
                observation_id=meta["observation_id"],
            ),
            "drag",
        )
        self.assertEqual(
            self._readout("lbl_drop"), before, "A refused drag still dropped something"
        )
        print("[Test 35] Drag refused; the application recorded no drop")

    # --- the refusal is honest about itself ------------------------------------

    def test_36_refusal_names_the_test_control_not_a_locked_session(self):
        """A refusal produced by the valve must not read as a real condition.

        An operator who sees "session is locked or headless" and believes it is
        chasing a genuine problem, when the engine was simply launched with a
        test variable set, has been told something false.
        """
        self._assert_foreground()
        meta = self._valved_observation()
        target = self.valved.target(bbox_frame_px=self._frame_target("pnl_click", meta))

        message = self._assert_refused(
            lambda: self.valved.click(observation_id=meta["observation_id"], target=target),
            "click",
        )
        self.assertIn(
            DESKTOP_VALVE_VAR,
            message,
            f"The refusal must name the test control that caused it, got {message!r}",
        )
        self.assertIn(
            "test control",
            message,
            f"The refusal must say it is not a real condition, got {message!r}",
        )
        print(f"[Test 36] Refusal is honest about its cause: {message}")

    # --- the recorded exemption ------------------------------------------------

    def test_37_focus_is_not_gated_by_the_desktop_check(self):
        """Window focus composes no desktop check, and that is deliberate.

        Focusing dispatches no input, so the harm this check prevents cannot come
        from it. It is asserted here so that the exemption is a pinned decision
        rather than an omission someone later reads as an oversight.
        """
        result = self.valved.focus_window(
            self.action_window["hwnd"],
            pid=self.action_window["pid"],
            process_create_time_utc=self.action_window.get("process_create_time_utc", "unknown"),
        )
        self.assertEqual(
            result.get("status"), "focused", f"Focus should be unaffected: {result}"
        )
        print("[Test 37] Window focus is unaffected by the desktop check, as recorded")


if __name__ == "__main__":
    unittest.main()
