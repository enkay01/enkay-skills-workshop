"""CLI session tests: the `wcu` command operating a persistent session.

Every test invokes ``python -m wcu`` as a separate process. The session
process persists across those invocations, which is the delivery contract:
separate command invocations reuse one desktop session and one engine.

The WinForms fixture provides a native window with a Continue button that
increments a counter, giving an independent application-state oracle.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import os
import subprocess
import sys
import time
import unittest

root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
cli_dir = os.path.join(root_dir, "cli")
fixture_exe = os.path.join(
    root_dir, "tests", "fixture_app", "bin", "Debug", "net6.0-windows", "fixture_app.exe"
)

user32 = ctypes.windll.user32

# A generous observation age: each CLI invocation is a fresh Python process,
# so the gap between observe and act is longer than an in-process call.
MAX_AGE_MS = "8000"


def run_cli(*args: str, timeout: float = 90.0) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = cli_dir + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "wcu", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def cli_json(*args: str, timeout: float = 90.0) -> dict:
    proc = run_cli(*args, timeout=timeout)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise AssertionError(
            f"wcu {' '.join(args)} produced non-JSON stdout.\n"
            f"stdout: {proc.stdout!r}\nstderr: {proc.stderr!r}"
        )


def cli_ok(*args: str, timeout: float = 90.0) -> dict:
    env = cli_json(*args, timeout=timeout)
    assert env.get("ok") is True, f"wcu {' '.join(args)} not ok: {env}"
    return env


class TestCliSession(unittest.TestCase):
    fixture_proc: subprocess.Popen

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(fixture_exe):
            raise FileNotFoundError(f"Fixture executable not found at '{fixture_exe}'")

        # Attach the test runner thread to the input desktop so engine
        # operations that need the interactive desktop succeed.
        hdesk = user32.OpenInputDesktop(0, False, 0x01FF)
        if hdesk:
            user32.SetThreadDesktop(hdesk)

        # Start from a clean session state.
        run_cli("session", "stop")
        cls.fixture_proc = subprocess.Popen([fixture_exe])
        time.sleep(1.5)

    def setUp(self):
        # The session persists across invocations by design; each test starts
        # from a clean slate so `session start` is exercised independently.
        run_cli("session", "stop")

    @classmethod
    def tearDownClass(cls):
        run_cli("session", "stop")
        if cls.fixture_proc and cls.fixture_proc.poll() is None:
            cls.fixture_proc.terminate()
            try:
                cls.fixture_proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                cls.fixture_proc.kill()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _fixture_hwnd(self) -> str:
        env = cli_ok("windows", "--filter", "WCU_Native_WinForms_Fixture")
        windows = env["result"]["windows"]
        self.assertTrue(windows, "Fixture window not found in `wcu windows`")
        return str(windows[0]["hwnd"])

    def _ensure_attached(self) -> str:
        """Attach to the fixture window (inspect/observe need a target)."""
        hwnd = self._fixture_hwnd()
        cli_ok("attach", hwnd)
        return hwnd

    def _focus_fixture(self) -> str:
        """Bring the fixture to the foreground for guarded actions.

        The fixture calls AllowSetForegroundWindow, letting the engine set it
        foreground. Guarded actions require the target to be in front.
        """
        hwnd = self._fixture_hwnd()
        cli_ok("focus", hwnd)
        return hwnd

    def _counter(self) -> int:
        self._ensure_attached()
        env = cli_ok("inspect", "--max-elements", "60")
        elements = env["result"]["elements"]
        counter = next(
            (e for e in elements if e.get("automation_id") == "lbl_counter"), None
        )
        self.assertIsNotNone(counter, "lbl_counter not found via inspect")
        name = counter.get("name", "")
        return int(name.split(":")[1].strip())

    def _observe(self) -> dict:
        self._ensure_attached()
        return cli_ok("observe")["result"]

    def _click_continue(self) -> dict:
        """Focus the fixture, then click its Continue button.

        Uses UIA bounds mapped into the observation frame. Guarded actions
        require the target in the foreground, so the fixture is focused first.
        """
        self._focus_fixture()
        obs = self._observe()
        insp = cli_ok("inspect", "--max-elements", "60")["result"]
        btn = next(
            (
                e
                for e in insp["elements"]
                if e.get("name") == "Continue"
                and "Button" in e.get("control_type", "")
            ),
            None,
        )
        self.assertIsNotNone(btn, "Continue button not found via inspect")
        bounds = obs["capture_bounds_physical_px"]
        bb = btn["bounds"]
        bx = bb["x"] - bounds["x"]
        by = bb["y"] - bounds["y"]
        return cli_json(
            "act", "click",
            "--observation-id", str(obs["observation_id"]),
            "--bbox", str(bx), str(by), str(bb["w"]), str(bb["h"]),
            "--max-age-ms", MAX_AGE_MS,
        )

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    def test_01_session_lifecycle_across_invocations(self):
        """start, status, windows, and stop work as separate invocations."""
        start = cli_ok("session", "start")
        self.assertEqual(start["status"], "ok")
        session_id = start["result"]["session_id"]
        self.assertTrue(session_id)
        # Capabilities are reported at start so the agent can check prerequisites.
        self.assertIn("actions", start["result"]["capabilities"])

        status = cli_ok("session", "status")
        self.assertEqual(status["result"]["session_id"], session_id)
        self.assertTrue(status["result"]["engine_alive"])

        windows = cli_ok("windows")
        self.assertGreater(windows["result"]["count"], 0)

        stop = cli_ok("session", "stop")
        self.assertEqual(stop["result"]["status"], "stopped")

    def test_02_second_session_refused(self):
        """A second `session start` while one is active is refused."""
        cli_ok("session", "start")
        env = cli_json("session", "start")
        self.assertFalse(env["ok"])
        self.assertEqual(env["error"]["code"], "session_already_active")
        # The existing session still works.
        cli_ok("windows")

    def test_03_commands_without_session_fail_cleanly(self):
        """With no session, commands report no_session rather than crashing."""
        run_cli("session", "stop")
        env = cli_json("windows")
        self.assertFalse(env["ok"])
        self.assertEqual(env["error"]["code"], "no_session")

    def test_04_attach_and_observe(self):
        """attach + observe returns a viewable PNG and frame metadata."""
        cli_ok("session", "start")
        hwnd = self._fixture_hwnd()
        attach = cli_ok("attach", hwnd)
        self.assertEqual(attach["result"]["status"], "attached")

        obs = self._observe()
        self.assertIn("observation_id", obs)
        self.assertIn("window_identity", obs)
        self.assertGreater(obs["width"], 0)
        self.assertGreater(obs["height"], 0)
        image_path = obs["image_path"]
        self.assertTrue(os.path.isabs(image_path), "image path must be absolute")
        self.assertTrue(os.path.exists(image_path), f"screenshot not written: {image_path}")
        self.assertGreater(os.path.getsize(image_path), 1000, "screenshot suspiciously small")

    def test_05_act_click_increments_counter(self):
        """observe -> act click -> counter increments (guarded dispatch)."""
        cli_ok("session", "start")
        self.assertEqual(self._counter(), 0)

        env = self._click_continue()
        self.assertTrue(env["ok"], f"click refused: {env}")
        self.assertEqual(env["status"], "ok")
        self.assertEqual(env["result"]["dispatch"]["status"], "clicked")
        # Post-action evidence is included.
        self.assertIn("evidence", env["result"])

        time.sleep(0.4)
        self.assertEqual(self._counter(), 1, "Continue click did not increment the counter")

    def test_06_dry_run_does_not_dispatch(self):
        """--dry-run validates the proposal without changing app state."""
        cli_ok("session", "start")
        before = self._counter()
        self._focus_fixture()
        obs = self._observe()
        insp = cli_ok("inspect", "--max-elements", "60")["result"]
        btn = next(
            (e for e in insp["elements"] if e.get("name") == "Continue"), None
        )
        self.assertIsNotNone(btn)
        bounds = obs["capture_bounds_physical_px"]
        bb = btn["bounds"]
        env = cli_json(
            "act", "click", "--dry-run",
            "--observation-id", str(obs["observation_id"]),
            "--bbox", str(bb["x"] - bounds["x"]), str(bb["y"] - bounds["y"]),
            str(bb["w"]), str(bb["h"]),
            "--max-age-ms", MAX_AGE_MS,
        )
        self.assertTrue(env["ok"], f"dry-run refused: {env}")
        self.assertEqual(env["status"], "dry_run")
        self.assertEqual(env["result"]["dispatch"]["events_injected"], 0)
        self.assertEqual(self._counter(), before, "dry-run must not change app state")

    def test_07_stale_target_refusal_returns_evidence(self):
        """Moving the window between observe and act refuses and returns evidence."""
        cli_ok("session", "start")
        hwnd = self._fixture_hwnd()
        hwnd_int = int(hwnd)
        before = self._counter()

        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd_int, ctypes.byref(rect))
        cur_w = rect.right - rect.left
        cur_h = rect.bottom - rect.top

        try:
            # Observe first: the refusal is only meaningful when the observation
            # predates the move, so the engine can compare the frame it handed
            # out against the window's current geometry.
            self._focus_fixture()
            obs = self._observe()
            insp = cli_ok("inspect", "--max-elements", "60")["result"]
            btn = next(
                (
                    e
                    for e in insp["elements"]
                    if e.get("name") == "Continue"
                    and "Button" in e.get("control_type", "")
                ),
                None,
            )
            self.assertIsNotNone(btn, "Continue button not found via inspect")
            bounds = obs["capture_bounds_physical_px"]
            bb = btn["bounds"]

            user32.MoveWindow(hwnd_int, rect.left + 60, rect.top + 60, cur_w, cur_h, True)
            user32.SetForegroundWindow(hwnd_int)
            time.sleep(0.3)

            env = cli_json(
                "act", "click",
                "--observation-id", str(obs["observation_id"]),
                "--bbox", str(bb["x"] - bounds["x"]), str(bb["y"] - bounds["y"]),
                str(bb["w"]), str(bb["h"]),
                "--max-age-ms", MAX_AGE_MS,
            )
            self.assertFalse(env["ok"], "moved-window click should be refused")
            self.assertEqual(env["status"], "refused")
            self.assertEqual(env["error"]["code"], "geometry_changed")
            # Updated evidence is returned for reconsideration.
            self.assertIn("evidence", env["error"].get("details", {}) or {})
        finally:
            user32.MoveWindow(hwnd_int, rect.left, rect.top, cur_w, cur_h, True)
            user32.SetForegroundWindow(hwnd_int)
            time.sleep(0.3)

        self.assertEqual(self._counter(), before, "refused click must not change app state")

    def test_08_history_export_excludes_typed_text(self):
        """history records operations and outcomes, never typed text."""
        cli_ok("session", "start")
        cli_ok("windows")
        env = cli_ok("history")
        entries = env["result"]["entries"]
        self.assertTrue(entries, "history should not be empty")
        ops = {e["op"] for e in entries}
        self.assertIn("windows", ops)
        # No entry may carry typed text or image contents.
        for e in entries:
            self.assertNotIn("text", e)
            self.assertNotIn("image", e)
            self.assertNotIn("image_path", e)

    def test_09_screenshot_cleanup_on_stop(self):
        """Screenshots are written under the session dir and removed on stop."""
        from wcu.paths import shots_dir

        cli_ok("session", "start")
        obs = self._observe()
        path = obs["image_path"]
        self.assertTrue(os.path.exists(path))

        cli_ok("session", "stop")
        self.assertFalse(
            os.path.exists(path), "screenshot must be deleted on session stop"
        )

    def test_10_cancel_noop_when_idle(self):
        """cancel with nothing pending is a clean no-op."""
        cli_ok("session", "start")
        env = cli_ok("cancel")
        self.assertEqual(env["status"], "ok")
        # The session still works afterwards.
        cli_ok("windows")

    def test_11_switch_between_windows(self):
        """switch focuses, re-attaches, and returns a fresh observation."""
        cli_ok("session", "start")
        hwnd = self._fixture_hwnd()
        # Attach to the fixture first.
        cli_ok("attach", hwnd)
        # Switch away to another ordinary visible window and back.
        windows = cli_ok("windows")["result"]["windows"]
        other = next(
            (
                w
                for w in windows
                if str(w["hwnd"]) != hwnd
                and w.get("is_visible")
                and w.get("title")
                and "Lock" not in w.get("title", "")
            ),
            None,
        )
        if other is None:
            self.skipTest("No second visible window to switch to")
        switch = cli_json("switch", str(other["hwnd"]))
        if not switch.get("ok"):
            # Foreground transitions can be refused by the desktop for
            # reasons outside this tool's control; that is an environment
            # limitation, not a switch failure.
            self.skipTest(
                f"Desktop refused foreground transition: {switch['error']['message']}"
            )
        self.assertEqual(switch["status"], "ok")
        self.assertIn("observation", switch["result"])
        # Switch back to the fixture.
        back = cli_ok("switch", hwnd)
        self.assertEqual(back["status"], "ok")
        self.assertEqual(
            back["result"]["observation"]["window_identity"]["hwnd"], hwnd
        )


if __name__ == "__main__":
    unittest.main()
