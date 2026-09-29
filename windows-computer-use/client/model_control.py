"""One bounded screenshot -> model -> guarded click -> verification session."""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from vision_model import ModelError, VisionModel
from wcu_client import WcuClient, WcuError


class SessionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Frame:
    meta: dict[str, Any]
    pixels: np.ndarray
    png: bytes
    width: int
    height: int


def decode_frame(meta: dict[str, Any], payload: bytes) -> Frame:
    width = int(meta["width"])
    height = int(meta["height"])
    stride = int(meta["stride_bytes"])
    if width <= 0 or height <= 0 or stride < width * 4 or len(payload) != stride * height:
        raise SessionError("Invalid image dimensions or payload")
    raw = np.frombuffer(payload, dtype=np.uint8).reshape(height, stride)
    pixels = raw[:, : width * 4].reshape(height, width, 4).copy()
    ok, encoded = cv2.imencode(".png", pixels)
    if not ok:
        raise SessionError("Could not encode screenshot")
    return Frame(meta, pixels, encoded.tobytes(), width, height)


def parse_decision(data: dict[str, Any], width: int, height: int) -> tuple[str, list[int] | None]:
    intent = data.get("intent")
    if intent in ("done", "cannot_decide") and set(data) == {"intent"}:
        return intent, None
    if intent != "click" or set(data) != {"intent", "bbox"}:
        raise SessionError("Model returned an unsupported action")
    box = data["bbox"]
    if not isinstance(box, list) or len(box) != 4 or any(type(v) is not int for v in box):
        raise SessionError("Model click rectangle must contain four integers")
    x, y, w, h = box
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > width or y + h > height:
        raise SessionError("Model click rectangle is outside the image")
    return intent, box


def target_changed(before: Frame, after: Frame, box: list[int]) -> bool:
    if before.width != after.width or before.height != after.height:
        return True
    keys = ("window_identity", "geometry_epoch", "foreground_epoch", "capture_bounds_physical_px")
    if any(before.meta.get(key) != after.meta.get(key) for key in keys):
        return True
    x, y, w, h = box
    old = before.pixels[y : y + h, x : x + w, :3].astype(np.int16)
    new = after.pixels[y : y + h, x : x + w, :3].astype(np.int16)
    changed = np.any(np.abs(old - new) > 24, axis=2)
    return float(np.mean(changed)) > 0.02


def run_session(
    client: WcuClient,
    model: VisionModel,
    task: str,
    *,
    execute: bool = False,
    max_decisions: int = 2,
    max_duration_sec: float = 120.0,
) -> dict[str, Any]:
    if max_decisions < 1 or max_duration_sec <= 0:
        raise ValueError("Decision and duration limits must be positive")
    deadline = time.monotonic() + max_duration_sec
    proposed: list[int] | None = None
    prior: Frame | None = None
    decisions = 0

    while decisions < max_decisions:
        if time.monotonic() >= deadline:
            return {"status": "timeout", "decisions": decisions}
        if prior is None:
            meta, payload = client.observe()
            prior = decode_frame(meta, payload)
        decisions += 1
        intent, proposed = parse_decision(
            model.decide(task, prior.png, prior.width, prior.height), prior.width, prior.height
        )
        if intent != "click":
            return {"status": intent, "decisions": decisions}
        if not execute:
            return {"status": "dry_run", "bbox": proposed, "decisions": decisions}
        if time.monotonic() >= deadline:
            return {"status": "timeout", "decisions": decisions}

        try:
            meta, payload = client.observe(after_frame_id=int(prior.meta["frame_id"]), timeout_ms=2000)
        except WcuError as exc:
            if exc.code == "no_fresh_frame":
                return {"status": "no_fresh_frame", "decisions": decisions}
            raise
        current = decode_frame(meta, payload)
        if current.meta["frame_id"] <= prior.meta["frame_id"]:
            return {"status": "no_fresh_frame", "decisions": decisions}
        if target_changed(prior, current, proposed):
            prior = current
            continue

        try:
            dispatch = client.click(
                observation_id=int(current.meta["observation_id"]),
                target_bbox_frame_px=proposed,
            )
        except WcuError as exc:
            return {"status": "action_rejected", "code": exc.code, "decisions": decisions}
        if dispatch.get("status") != "clicked":
            return {"status": "action_rejected", "code": "input_failed", "decisions": decisions}
        time.sleep(0.3)
        try:
            meta, payload = client.observe(after_frame_id=int(current.meta["frame_id"]), timeout_ms=3000)
        except WcuError as exc:
            return {"status": "verification_unavailable", "code": exc.code, "decisions": decisions}
        after = decode_frame(meta, payload)
        try:
            verdict = model.verify(task, current.png, after.png, after.width, after.height)
        except ModelError:
            return {"status": "verification_unavailable", "code": "model_response_invalid", "decisions": decisions, "action_status": dispatch["status"]}
        if set(verdict) != {"result"} or verdict["result"] not in ("success", "failure", "uncertain"):
            return {"status": "verification_unavailable", "code": "model_verdict_invalid", "decisions": decisions, "action_status": dispatch["status"]}
        return {
            "status": "verified" if verdict["result"] == "success" else "unverified",
            "model_verdict": verdict["result"],
            "bbox": proposed,
            "decisions": decisions,
            "action_status": dispatch["status"],
        }
    return {"status": "stale_target", "decisions": decisions}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one model-controlled desktop click")
    parser.add_argument("--task", required=True)
    parser.add_argument("--window-title", required=True, help="Unique substring of a visible window title")
    parser.add_argument("--model", default=os.getenv("WCU_MODEL"))
    parser.add_argument("--endpoint", default=os.getenv("WCU_MODEL_ENDPOINT", "http://127.0.0.1:8317/v1/chat/completions"))
    parser.add_argument("--execute", action="store_true", help="Permit one guarded desktop click")
    args = parser.parse_args()
    token = os.getenv("WCU_PROXY_TOKEN", "")
    if not args.model or not token:
        parser.error("Set --model (or WCU_MODEL) and WCU_PROXY_TOKEN")
    model = VisionModel(args.endpoint, args.model, token)
    try:
        with WcuClient() as client:
            matches = [w for w in client.list_windows() if args.window_title.lower() in w.get("title", "").lower()]
            if len(matches) != 1:
                raise SessionError(f"Expected exactly one matching window, found {len(matches)}")
            window = matches[0]
            client.attach(window["hwnd"], window["pid"], window["process_create_time_utc"])
            result = run_session(client, model, args.task, execute=args.execute)
            print(json.dumps(result, sort_keys=True))
    except (WcuError, ModelError, SessionError) as exc:
        parser.exit(1, f"Session stopped: {exc}\n")


if __name__ == "__main__":
    main()
