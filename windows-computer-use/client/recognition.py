"""OCR and Visual Target Recognition for Windows Computer Use.

Provides persistent RapidOCR engine management, BGRA buffer decoding,
ROI cropping with coordinate re-projection, and strict profile-based gating.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
from rapidocr_onnxruntime import RapidOCR


@dataclass
class TargetBox:
    label: str
    bbox: Tuple[int, int, int, int]  # (x, y, width, height) in physical frame pixels
    polygon: List[List[float]]  # 4 points [[x, y], ...] in full frame coordinates
    confidence: float

    @property
    def center(self) -> Tuple[int, int]:
        x, y, w, h = self.bbox
        return x + w // 2, y + h // 2


@dataclass
class Profile:
    name: str
    process_name: str
    window_title_pattern: str
    reference_frame: str
    allowed_dimensions: Tuple[int, int]  # (width, height)
    target_roi: Tuple[float, float, float, float]  # (x_min, y_min, x_max, y_max) normalized 0.0-1.0
    accepted_labels: List[str]
    stop_labels: List[str]
    expected_transition: str
    min_confidence: float = 0.50

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Profile:
        return cls(
            name=data["name"],
            process_name=data["process_name"],
            window_title_pattern=data["window_title_pattern"],
            reference_frame=data["reference_frame"],
            allowed_dimensions=tuple(data["allowed_dimensions"]),  # type: ignore
            target_roi=tuple(data["target_roi"]),  # type: ignore
            accepted_labels=data.get("accepted_labels", ["Continue"]),
            stop_labels=data.get("stop_labels", ["Must Respond"]),
            expected_transition=data.get("expected_transition", "state_changed"),
            min_confidence=data.get("min_confidence", 0.50),
        )

    @classmethod
    def from_file(cls, path: str) -> Profile:
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))


@dataclass
class RecognitionOutcome:
    status: str  # "target_found", "stop_label", "no_target", "ambiguous_target", "geometry_mismatch", "unsupported_language"
    target: Optional[TargetBox] = None
    stop_label: Optional[str] = None
    all_candidates: List[TargetBox] = field(default_factory=list)
    ocr_latency_ms: float = 0.0
    reason: str = ""
    full_text: List[str] = field(default_factory=list)


class OcrRecognizer:
    _instance: Optional[OcrRecognizer] = None

    def __init__(self):
        t0 = time.perf_counter()
        self._engine = RapidOCR()
        self.cold_start_ms = (time.perf_counter() - t0) * 1000.0
        self._warmed_up = False

    @classmethod
    def get_instance(cls) -> OcrRecognizer:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def warmup(self) -> float:
        """Warm up ONNX runtime by executing inference on a dummy frame."""
        dummy = np.zeros((100, 300, 3), dtype=np.uint8)
        cv2.putText(dummy, "WARMUP", (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
        t0 = time.perf_counter()
        self._engine(dummy)
        warmup_ms = (time.perf_counter() - t0) * 1000.0
        self._warmed_up = True
        return warmup_ms

    @staticmethod
    def frame_from_bgra_buffer(
        payload: bytes, width: int, height: int, stride_bytes: int
    ) -> np.ndarray:
        """Decode raw BGRA bytes with stride padding into a contiguous BGR image."""
        expected_bytes = stride_bytes * height
        if len(payload) < expected_bytes:
            raise ValueError(
                f"Payload length {len(payload)} is smaller than expected buffer size {expected_bytes} "
                f"({height} lines x {stride_bytes} bytes stride)"
            )

        # Reshape directly from memory buffer
        raw_arr = np.frombuffer(payload[:expected_bytes], dtype=np.uint8).reshape((height, stride_bytes))
        # Stride is in bytes; each BGRA pixel is 4 bytes
        actual_row_pixels = stride_bytes // 4
        bgra = raw_arr.reshape((height, actual_row_pixels, 4))
        # Crop width if stride included padding
        if actual_row_pixels > width:
            bgra = bgra[:, :width, :]

        # Convert to BGR (standard for OpenCV and RapidOCR)
        return cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)

    def recognize(self, image: np.ndarray, profile: Profile) -> RecognitionOutcome:
        """Recognize actionable targets or stop conditions according to a profile.
        
        Strict safety gates:
        1. Validates physical image dimensions against profile.allowed_dimensions.
        2. Crops to profile.target_roi to bound OCR computation and prevent background false positives.
        3. Remaps polygon and bounding box coordinates back to full-frame pixels.
        4. Detects stop labels ('Must Respond') -> immediately abstains with status='stop_label'.
        5. Matches accepted labels ('Continue') strictly.
        6. Rejects ambiguity (multiple candidates in ROI) -> status='ambiguous_target'.
        7. Returns detailed latency and reason metadata.
        """
        img_h, img_w = image.shape[:2]
        allowed_w, allowed_h = profile.allowed_dimensions

        if (img_w, img_h) != (allowed_w, allowed_h):
            return RecognitionOutcome(
                status="geometry_mismatch",
                reason=f"Frame dimensions ({img_w}x{img_h}) do not match profile requirement ({allowed_w}x{allowed_h})",
            )

        # Compute ROI bounding slice
        rx_min, ry_min, rx_max, ry_max = profile.target_roi
        crop_x1 = max(0, int(round(rx_min * img_w)))
        crop_y1 = max(0, int(round(ry_min * img_h)))
        crop_x2 = min(img_w, int(round(rx_max * img_w)))
        crop_y2 = min(img_h, int(round(ry_max * img_h)))

        if crop_x2 <= crop_x1 or crop_y2 <= crop_y1:
            return RecognitionOutcome(
                status="geometry_mismatch",
                reason=f"Invalid ROI bounds: ({crop_x1}, {crop_y1}, {crop_x2}, {crop_y2})",
            )

        roi_crop = image[crop_y1:crop_y2, crop_x1:crop_x2]

        t0 = time.perf_counter()
        ocr_res, _ = self._engine(roi_crop)
        ocr_ms = (time.perf_counter() - t0) * 1000.0

        if not ocr_res:
            return RecognitionOutcome(
                status="no_target",
                ocr_latency_ms=ocr_ms,
                reason="No text detected in target ROI",
            )

        full_text = []
        parsed_boxes: List[TargetBox] = []

        for item in ocr_res:
            poly_roi, text, conf = item
            clean_text = text.strip()
            full_text.append(clean_text)

            # Map polygon back to full frame
            poly_full = [[float(pt[0] + crop_x1), float(pt[1] + crop_y1)] for pt in poly_roi]
            xs = [pt[0] for pt in poly_full]
            ys = [pt[1] for pt in poly_full]
            bx = int(round(min(xs)))
            by = int(round(min(ys)))
            bw = int(round(max(xs) - min(xs)))
            bh = int(round(max(ys) - min(ys)))

            parsed_boxes.append(
                TargetBox(
                    label=clean_text,
                    bbox=(bx, by, bw, bh),
                    polygon=poly_full,
                    confidence=float(conf),
                )
            )

        # 1. Check for STOP labels (e.g. 'Must Respond')
        normalized_stop = [s.strip().lower() for s in profile.stop_labels]
        for box in parsed_boxes:
            box_text_norm = box.label.strip().lower()
            for s in normalized_stop:
                if s == box_text_norm or s in box_text_norm:
                    return RecognitionOutcome(
                        status="stop_label",
                        stop_label=box.label,
                        all_candidates=parsed_boxes,
                        ocr_latency_ms=ocr_ms,
                        reason=f"Stop label '{box.label}' detected in ROI (matches '{s}')",
                        full_text=full_text,
                    )

        # 2. Check for ACCEPTED labels (e.g. 'Continue')
        normalized_accepted = [a.strip().lower() for a in profile.accepted_labels]
        matching_targets: List[TargetBox] = []
        for box in parsed_boxes:
            if box.confidence < profile.min_confidence:
                continue
            box_text_norm = box.label.strip().lower()
            for acc in normalized_accepted:
                # Require exact word or normalized match
                if box_text_norm == acc or re.fullmatch(rf"\b{re.escape(acc)}\b", box_text_norm):
                    matching_targets.append(box)
                    break

        if len(matching_targets) == 0:
            return RecognitionOutcome(
                status="no_target",
                all_candidates=parsed_boxes,
                ocr_latency_ms=ocr_ms,
                reason=f"No accepted label ({profile.accepted_labels}) found in ROI. Detected: {full_text}",
                full_text=full_text,
            )

        if len(matching_targets) > 1:
            return RecognitionOutcome(
                status="ambiguous_target",
                all_candidates=matching_targets,
                ocr_latency_ms=ocr_ms,
                reason=f"Ambiguous: found {len(matching_targets)} matching targets in ROI",
                full_text=full_text,
            )

        # Exactly 1 target found
        target = matching_targets[0]
        return RecognitionOutcome(
            status="target_found",
            target=target,
            all_candidates=parsed_boxes,
            ocr_latency_ms=ocr_ms,
            reason=f"Found single target '{target.label}' with confidence {target.confidence:.2f}",
            full_text=full_text,
        )
