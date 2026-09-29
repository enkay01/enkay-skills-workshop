"""Behavior at the model/session/desktop-client seam."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))

from model_control import SessionError, run_session
from vision_model import ModelError
from wcu_client import WcuError


def frame(frame_id: int, *, target_changed: bool = False) -> tuple[dict, bytes]:
    pixels = np.full((80, 100, 4), 255, dtype=np.uint8)
    if target_changed:
        pixels[10:30, 10:30, :3] = 0
    meta = {
        "observation_id": frame_id,
        "frame_id": frame_id,
        "width": 100,
        "height": 80,
        "stride_bytes": 400,
        "window_identity": {"hwnd": "1", "pid": 2, "process_create_time_utc": "x"},
        "geometry_epoch": 1,
        "foreground_epoch": 1,
        "capture_bounds_physical_px": {"x": 0, "y": 0, "w": 100, "h": 80},
    }
    return meta, pixels.tobytes()


class DesktopClient:
    def __init__(self, frames: list[tuple[dict, bytes]]):
        self.frames = iter(frames)
        self.clicks: list[tuple[int, list[int]]] = []

    def observe(self, after_frame_id: int = 0, timeout_ms: int = 2000):
        try:
            result = next(self.frames)
        except StopIteration as exc:
            raise WcuError("no_fresh_frame", "No new frame") from exc
        if result[0]["frame_id"] <= after_frame_id:
            raise WcuError("no_fresh_frame", "No new frame")
        return result

    def click(self, observation_id: int, target_bbox_frame_px: list[int]):
        self.clicks.append((observation_id, target_bbox_frame_px))
        return {"status": "clicked"}


class Model:
    def __init__(self, decisions: list[dict], verdict: str = "success"):
        self.decisions = iter(decisions)
        self.verdict = verdict
        self.images_seen = 0

    def decide(self, task: str, png: bytes, width: int, height: int):
        assert png.startswith(b"\x89PNG") and (width, height) == (100, 80)
        self.images_seen += 1
        return next(self.decisions)

    def verify(self, task: str, before_png: bytes, after_png: bytes, width: int, height: int):
        assert before_png.startswith(b"\x89PNG") and after_png.startswith(b"\x89PNG")
        self.images_seen += 2
        return {"result": self.verdict}


class ModelControlTests(unittest.TestCase):
    def test_one_model_click_is_verified_against_a_later_frame(self):
        client = DesktopClient([frame(1), frame(2), frame(3, target_changed=True)])
        model = Model([{"intent": "click", "bbox": [10, 10, 20, 20]}])

        result = run_session(client, model, "Click Continue", execute=True)

        self.assertEqual(result["status"], "verified")
        self.assertEqual(client.clicks, [(2, [10, 10, 20, 20])])
        self.assertEqual(model.images_seen, 3)

    def test_changed_target_requires_new_model_decision_and_no_click(self):
        client = DesktopClient([frame(1), frame(2, target_changed=True)])
        model = Model([{"intent": "click", "bbox": [10, 10, 20, 20]}, {"intent": "cannot_decide"}])

        result = run_session(client, model, "Click Continue", execute=True)

        self.assertEqual(result["status"], "cannot_decide")
        self.assertEqual(client.clicks, [])
        self.assertEqual(model.images_seen, 2)

    def test_no_new_frame_stops_before_input(self):
        client = DesktopClient([frame(1)])
        model = Model([{"intent": "click", "bbox": [10, 10, 20, 20]}])

        result = run_session(client, model, "Click Continue", execute=True)

        self.assertEqual(result["status"], "no_fresh_frame")
        self.assertEqual(client.clicks, [])

    def test_invalid_model_target_cannot_reach_input(self):
        client = DesktopClient([frame(1)])
        model = Model([{"intent": "click", "bbox": [100, 10, 20, 20]}])

        with self.assertRaises(SessionError):
            run_session(client, model, "Click Continue", execute=True)
        self.assertEqual(client.clicks, [])

    def test_verification_failure_does_not_repeat_an_executed_click(self):
        client = DesktopClient([frame(1), frame(2), frame(3, target_changed=True)])

        class FailingVerification(Model):
            def verify(self, task: str, before_png: bytes, after_png: bytes, width: int, height: int):
                raise ModelError("empty model response")

        model = FailingVerification([{"intent": "click", "bbox": [10, 10, 20, 20]}])
        result = run_session(client, model, "Click Continue", execute=True)

        self.assertEqual(result["status"], "verification_unavailable")
        self.assertEqual(client.clicks, [(2, [10, 10, 20, 20])])


if __name__ == "__main__":
    unittest.main()
