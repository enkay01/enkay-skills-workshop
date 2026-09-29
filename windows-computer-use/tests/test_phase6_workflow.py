"""Phase 6 Verification Suite: Bounded Continue Workflow State Machine.

Verifies:
1. Default dry-run mode simulates exactly 1 action without dispatching SendInput.
2. Invariant: max_actions=0 halts immediately and does not mean unlimited.
3. Stop label ('Must Respond') immediately forces state transition to NEEDS_DECISION and halts input.
4. User cancellation signal immediately transitions to STOPPED.
5. Evidence frames and action logs are recorded.
"""

from __future__ import annotations

import os
import sys
import unittest
import numpy as np

# Add client and parent to sys.path
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
client_dir = os.path.join(root_dir, "client")
if client_dir not in sys.path:
    sys.path.insert(0, client_dir)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from fm_continue import ContinueStateMachine, WorkflowState, WorkflowTelemetry
from recognition import Profile, TargetBox


class MockWcuClient:
    """Mock client for verifying state machine logic and transitions without live OS dependencies."""
    def __init__(self, frames_sequence=None):
        self.frames_sequence = frames_sequence or []
        self._frame_idx = 0
        self.clicks = []
        self.attached = False

    def attach(self, hwnd, pid, create_time):
        self.attached = True

    def detach(self):
        self.attached = False

    def observe(self, after_frame_id=0, timeout_ms=2000):
        if not self.frames_sequence:
            # Default empty frame
            img = np.zeros((1680, 2520, 3), dtype=np.uint8)
            payload = img.tobytes()
            meta = {
                "observation_id": self._frame_idx + 1,
                "frame_id": self._frame_idx + 1,
                "width": 2520,
                "height": 1680,
                "stride_bytes": 2520 * 4,
            }
            self._frame_idx += 1
            return meta, payload

        frame_data = self.frames_sequence[min(self._frame_idx, len(self.frames_sequence) - 1)]
        self._frame_idx += 1
        return frame_data

    def click(self, observation_id, target_bbox_frame_px, max_age_ms=500):
        self.clicks.append((observation_id, target_bbox_frame_px))
        return {"status": "clicked", "observation_id": observation_id}


class TestPhase6Workflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = Profile.from_file(os.path.join(root_dir, "profiles", "fm24.example.json"))

    def test_01_dry_run_workflow_simulates_action(self):
        """Verify that default dry-run mode simulates action and halts at max_actions."""
        import cv2
        # Frame with Continue button
        img_path = os.path.join(root_dir, "tests", "fixtures", "labeled_frames", "fm_continue_present.png")
        bgr = cv2.imread(img_path)
        h, w = bgr.shape[:2]
        bgra = cv2.cvtColor(bgr, cv2.COLOR_BGR2BGRA)
        payload = bgra.tobytes()
        meta = {
            "observation_id": 1,
            "frame_id": 1,
            "width": w,
            "height": h,
            "stride_bytes": w * 4,
        }

        mock_client = MockWcuClient(frames_sequence=[(meta, payload), (meta, payload), (meta, payload)])
        sm = ContinueStateMachine(
            client=mock_client,
            profile=self.profile,
            hwnd="12345",
            pid=9999,
            process_create_time="123456",
            dry_run=True,
            max_actions=1,
            timeout_sec=5.0,
        )

        telemetry = sm.run()
        self.assertEqual(telemetry.state, WorkflowState.STOPPED.value)
        self.assertEqual(telemetry.actions_executed, 1)
        self.assertEqual(len(mock_client.clicks), 0, "Dry run must NOT call client.click")
        self.assertIn("action_limit_reached", telemetry.stop_reason)
        print(f"\n[Test 1] Dry run simulated action successfully without SendInput: {telemetry.stop_reason}")

    def test_02_zero_max_actions_stops_immediately(self):
        """Invariant: max_actions=0 must not mean unlimited; it must halt immediately."""
        mock_client = MockWcuClient()
        sm = ContinueStateMachine(
            client=mock_client,
            profile=self.profile,
            hwnd="12345",
            pid=9999,
            process_create_time="123456",
            dry_run=True,
            max_actions=0,
            timeout_sec=5.0,
        )

        telemetry = sm.run()
        self.assertEqual(telemetry.state, WorkflowState.STOPPED.value)
        self.assertEqual(telemetry.actions_executed, 0)
        self.assertEqual(telemetry.stop_reason, "max_actions_zero")
        self.assertEqual(len(mock_client.clicks), 0)
        print(f"\n[Test 2] Zero action limit cleanly halted: {telemetry.stop_reason}")

    def test_03_stop_label_halts_input(self):
        """Verify that stop label ('Must Respond') enters NEEDS_DECISION and prevents any click."""
        import cv2
        # Frame with Must Respond button
        img_path = os.path.join(root_dir, "tests", "fixtures", "labeled_frames", "fm_must_respond_present.png")
        bgr = cv2.imread(img_path)
        h, w = bgr.shape[:2]
        bgra = cv2.cvtColor(bgr, cv2.COLOR_BGR2BGRA)
        payload = bgra.tobytes()
        meta = {
            "observation_id": 1,
            "frame_id": 1,
            "width": w,
            "height": h,
            "stride_bytes": w * 4,
        }

        mock_client = MockWcuClient(frames_sequence=[(meta, payload)])
        sm = ContinueStateMachine(
            client=mock_client,
            profile=self.profile,
            hwnd="12345",
            pid=9999,
            process_create_time="123456",
            dry_run=False,  # Live execution mode
            max_actions=5,
            timeout_sec=5.0,
        )

        telemetry = sm.run()
        self.assertEqual(telemetry.state, WorkflowState.STOPPED.value)
        self.assertEqual(telemetry.actions_executed, 0)
        self.assertEqual(telemetry.stop_reason, "decision_required")
        self.assertEqual(len(mock_client.clicks), 0, "Must Respond must NEVER trigger click")
        self.assertTrue(len(telemetry.evidence_saved) > 0, "Evidence frame must be saved on stop condition")
        print(f"\n[Test 3] Stop condition correctly halted automation: {telemetry.stop_reason}, evidence: {telemetry.evidence_saved}")

    def test_04_cancellation_halts_input(self):
        """Verify that user cancellation halts state machine immediately."""
        mock_client = MockWcuClient()
        sm = ContinueStateMachine(
            client=mock_client,
            profile=self.profile,
            hwnd="12345",
            pid=9999,
            process_create_time="123456",
            dry_run=True,
            max_actions=10,
            timeout_sec=5.0,
        )
        sm.cancel()
        telemetry = sm.run()
        self.assertEqual(telemetry.state, WorkflowState.STOPPED.value)
        self.assertEqual(telemetry.actions_executed, 0)
        self.assertEqual(telemetry.stop_reason, "user_cancelled")
        print(f"\n[Test 4] Cancellation handled cleanly: {telemetry.stop_reason}")


if __name__ == "__main__":
    unittest.main()
