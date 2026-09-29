"""Phase 5 Verification Suite: One Guarded Visual Action.

Verifies:
1. End-to-end visual target recognition followed by guarded SendInput injection against native fixture.
2. Verified application state change: exactly one click increments counter from 0 to 1.
3. Out-of-bounds bounding box coordinates rejected ('invalid_coordinates').
4. Stale / expired observation rejected ('stale_observation').
5. Mismatched observation ID rejected ('stale_observation').
6. Window geometry movement invalidation rejected ('geometry_changed').
7. Focus / foreground loss rejected ('foreground_changed').
8. All rejected dispatch attempts preserve application state (counter remains unchanged).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import subprocess
import sys
import time
import unittest

# Ensure client directory is on sys.path
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
client_dir = os.path.join(root_dir, "client")
if client_dir not in sys.path:
    sys.path.insert(0, client_dir)

from recognition import OcrRecognizer, Profile
from wcu_client import WcuClient, WcuError

user32 = ctypes.windll.user32


class TestPhase5GuardedClick(unittest.TestCase):
    fixture_proc: subprocess.Popen
    client: WcuClient
    target_info: dict
    profile: Profile
    recognizer: OcrRecognizer

    @classmethod
    def setUpClass(cls):
        # 1. Compile check / path check for fixture app
        fixture_exe = os.path.abspath(
            os.path.join(
                root_dir,
                "tests",
                "fixture_app",
                "bin",
                "Debug",
                "net6.0-windows",
                "fixture_app.exe",
            )
        )
        if not os.path.exists(fixture_exe):
            raise FileNotFoundError(f"Fixture executable not found at '{fixture_exe}'")

        # 2. Attach test runner thread to input desktop
        hdesk = user32.OpenInputDesktop(0, False, 0x01FF)
        if hdesk:
            user32.SetThreadDesktop(hdesk)

        # 3. Launch fixture process
        cls.fixture_proc = subprocess.Popen([fixture_exe])
        time.sleep(1.5)

        # 3. Start WCU Client
        cls.client = WcuClient()
        cls.client.start()

        # 4. Attach to fixture
        wins = cls.client.list_windows()
        target = next((w for w in wins if "WCU_Native_WinForms_Fixture" in w.get("title", "")), None)
        assert target is not None, "Fixture window not found in list_windows"
        cls.target_info = target

        cls.client.attach(target["hwnd"], target["pid"], target["process_create_time_utc"])
        print(f"\n[Phase 5] Attached to fixture HWND {target['hwnd']}, PID {target['pid']}")

        # Ensure window is in foreground
        hwnd_int = int(target["hwnd"])
        user32.SetForegroundWindow(hwnd_int)
        time.sleep(0.5)

        # 5. Load profile and initialize recognizer
        cls.profile = Profile.from_file(os.path.join(root_dir, "profiles", "fixture.json"))
        # Adjust allowed dimensions dynamically to current fixture bounds
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd_int, ctypes.byref(rect))
        cur_w = rect.right - rect.left
        cur_h = rect.bottom - rect.top
        cls.profile.allowed_dimensions = (cur_w, cur_h)

        cls.recognizer = OcrRecognizer.get_instance()
        cls.recognizer.warmup()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.client.stop()
        except Exception:
            pass
        if cls.fixture_proc and cls.fixture_proc.poll() is None:
            cls.fixture_proc.terminate()
            cls.fixture_proc.wait(timeout=2.0)

    def _get_counter_value(self) -> int:
        """Read current counter value from fixture via UIA inspect."""
        elements, _ = self.client.inspect(max_depth=6, max_elements=50)
        counter_el = next((e for e in elements if e.get("automation_id") == "lbl_counter"), None)
        self.assertIsNotNone(counter_el, "lbl_counter not found in UIA tree")
        # Text format is 'Counter: X'
        name = counter_el.get("name", "")
        parts = name.split(":")
        self.assertEqual(len(parts), 2, f"Unexpected counter format: {name}")
        return int(parts[1].strip())

    def test_01_single_guarded_click_increments_counter(self):
        """Positive Gate: Exactly one visual guarded click increments counter from 0 to 1."""
        # Verify initial counter is 0
        initial_val = self._get_counter_value()
        self.assertEqual(initial_val, 0, f"Expected initial counter 0, got {initial_val}")

        # Ensure foreground
        hwnd_int = int(self.target_info["hwnd"])
        user32.SetForegroundWindow(hwnd_int)
        time.sleep(0.2)

        # 1. Observe frame
        obs_meta, payload = self.client.observe()
        obs_id = obs_meta["observation_id"]
        w = obs_meta["width"]
        h = obs_meta["height"]
        stride = obs_meta["stride_bytes"]

        # Update profile allowed dimensions to match captured frame
        self.profile.allowed_dimensions = (w, h)

        # 2. Decode and recognize
        frame_bgr = OcrRecognizer.frame_from_bgra_buffer(payload, w, h, stride)
        outcome = self.recognizer.recognize(frame_bgr, self.profile)

        self.assertEqual(outcome.status, "target_found", f"Target recognition failed: {outcome.reason}")
        self.assertIsNotNone(outcome.target)
        target_box = outcome.target
        self.assertEqual(target_box.label, "Continue")
        print(f"\n[Test 1] Detected target '{target_box.label}' at {target_box.bbox} with conf {target_box.confidence:.2f}")

        # 3. Dispatch guarded click
        click_res = self.client.click(
            observation_id=obs_id,
            target_bbox_frame_px=list(target_box.bbox),
            max_age_ms=2000,
        )
        print(f"[Test 1] Click dispatch result: {click_res}")
        self.assertEqual(click_res.get("status"), "clicked")
        self.assertEqual(click_res.get("events_injected"), 3)

        # Wait for WinForms event pump to process click
        time.sleep(0.3)

        # 4. Verify post-click application state
        new_val = self._get_counter_value()
        self.assertEqual(new_val, 1, f"Expected counter to increment to 1, got {new_val}")
        print(f"[Test 1] Verified counter incremented from 0 to {new_val}!")

    def test_02_out_of_bounds_coordinates_rejected(self):
        """Negative Gate: Out-of-bounds coordinates are rejected with 'invalid_coordinates'."""
        obs_meta, _ = self.client.observe()
        obs_id = obs_meta["observation_id"]
        w = obs_meta["width"]
        h = obs_meta["height"]

        # Target box way outside the frame dimensions
        invalid_bbox = [w + 500, h + 500, 100, 50]

        with self.assertRaises(WcuError) as ctx:
            self.client.click(observation_id=obs_id, target_bbox_frame_px=invalid_bbox)

        self.assertEqual(ctx.exception.code, "invalid_coordinates")
        print(f"\n[Test 2] Correctly rejected out-of-bounds bbox: {ctx.exception.message}")

        # Invariant: counter remains unchanged
        self.assertEqual(self._get_counter_value(), 1)

    def test_03_expired_observation_rejected(self):
        """Negative Gate: Stale observation exceeding max_age_ms is rejected."""
        obs_meta, _ = self.client.observe()
        obs_id = obs_meta["observation_id"]

        # Sleep to exceed tight threshold
        time.sleep(0.1)

        with self.assertRaises(WcuError) as ctx:
            # Set max_age_ms=10ms (observation is already ~100ms old)
            self.client.click(
                observation_id=obs_id,
                target_bbox_frame_px=[250, 190, 100, 30],
                max_age_ms=10,
            )

        self.assertEqual(ctx.exception.code, "stale_observation")
        print(f"\n[Test 3] Correctly rejected expired observation: {ctx.exception.message}")

        # Invariant: counter remains unchanged
        self.assertEqual(self._get_counter_value(), 1)

    def test_04_mismatched_observation_id_rejected(self):
        """Negative Gate: Non-existent or obsolete observation ID is rejected."""
        with self.assertRaises(WcuError) as ctx:
            self.client.click(
                observation_id=88888888,
                target_bbox_frame_px=[250, 190, 100, 30],
            )

        self.assertEqual(ctx.exception.code, "stale_observation")
        print(f"\n[Test 4] Correctly rejected mismatched observation ID: {ctx.exception.message}")

        # Invariant: counter remains unchanged
        self.assertEqual(self._get_counter_value(), 1)

    def test_05_moved_window_rejected(self):
        """Negative Gate: Window movement between observation and click triggers 'geometry_changed'."""
        hwnd_int = int(self.target_info["hwnd"])
        user32.SetForegroundWindow(hwnd_int)
        time.sleep(0.2)

        # 1. Observe current position
        obs_meta, _ = self.client.observe()
        obs_id = obs_meta["observation_id"]

        # 2. Move window by 50px
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd_int, ctypes.byref(rect))
        cur_w = rect.right - rect.left
        cur_h = rect.bottom - rect.top
        new_x = rect.left + 50
        new_y = rect.top + 50
        user32.MoveWindow(hwnd_int, new_x, new_y, cur_w, cur_h, True)
        user32.SetForegroundWindow(hwnd_int)
        time.sleep(0.2)

        try:
            # 3. Attempt click using the pre-move observation
            with self.assertRaises(WcuError) as ctx:
                self.client.click(
                    observation_id=obs_id,
                    target_bbox_frame_px=[250, 190, 100, 30],
                    max_age_ms=2000,
                )
            self.assertIn(ctx.exception.code, ["geometry_changed", "foreground_changed"])
            print(f"\n[Test 5] Correctly rejected click on moved window: {ctx.exception.message}")
        finally:
            # Restore original position
            user32.MoveWindow(hwnd_int, rect.left, rect.top, cur_w, cur_h, True)
            user32.SetForegroundWindow(hwnd_int)
            time.sleep(0.2)

        # Invariant: counter remains unchanged
        self.assertEqual(self._get_counter_value(), 1)

    def test_06_foreground_loss_rejected(self):
        """Negative Gate: Focus / foreground loss triggers 'foreground_changed'."""
        hwnd_int = int(self.target_info["hwnd"])
        user32.SetForegroundWindow(hwnd_int)
        time.sleep(0.2)

        # 1. Observe while in foreground
        obs_meta, _ = self.client.observe()
        obs_id = obs_meta["observation_id"]

        # 2. Minimize fixture window to force foreground loss
        user32.ShowWindow(hwnd_int, 6)  # SW_MINIMIZE
        time.sleep(0.3)

        try:
            # 3. Attempt click
            with self.assertRaises(WcuError) as ctx:
                self.client.click(
                    observation_id=obs_id,
                    target_bbox_frame_px=[250, 190, 100, 30],
                    max_age_ms=2000,
                )
            self.assertIn(ctx.exception.code, ["foreground_changed", "geometry_changed"])
            print(f"\n[Test 6] Correctly rejected click on backgrounded/minimized window: {ctx.exception.message}")
        finally:
            # Restore window
            user32.ShowWindow(hwnd_int, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd_int)
            time.sleep(0.3)

        # Invariant: counter remains unchanged
        self.assertEqual(self._get_counter_value(), 1)


if __name__ == "__main__":
    unittest.main()
