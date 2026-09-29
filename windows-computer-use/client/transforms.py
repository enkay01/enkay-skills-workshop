"""Coordinate Transformation Model and Image Utilities.

Handles scaling, aspect-ratio preservation, cropping, and exact bidirectional
mapping between model image space, raw capture frame space, and physical virtual
desktop coordinates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, Tuple, Union

import cv2
import numpy as np


Point = Tuple[int, int]
BoundingBox = Tuple[int, int, int, int]


@dataclass(frozen=True)
class CoordinateTransform:
    original_dimensions: Tuple[int, int]
    target_dimensions: Tuple[int, int]
    scale_factors: Tuple[float, float]
    crop_offset: Tuple[int, int]
    crop_size: Tuple[int, int]
    physical_origin: Tuple[int, int]

    @classmethod
    def identity(
        cls,
        dimensions: Tuple[int, int],
        physical_origin: Tuple[int, int] = (0, 0),
    ) -> CoordinateTransform:
        return cls(
            original_dimensions=dimensions,
            target_dimensions=dimensions,
            scale_factors=(1.0, 1.0),
            crop_offset=(0, 0),
            crop_size=dimensions,
            physical_origin=physical_origin,
        )

    @classmethod
    def from_fit(
        cls,
        original_dimensions: Tuple[int, int],
        max_dimensions: Tuple[int, int],
        physical_origin: Tuple[int, int] = (0, 0),
    ) -> CoordinateTransform:
        orig_w, orig_h = original_dimensions
        max_w, max_h = max_dimensions
        if orig_w <= 0 or orig_h <= 0 or max_w <= 0 or max_h <= 0:
            raise ValueError(f"Dimensions must be positive: {original_dimensions}, {max_dimensions}")

        scale = min(max_w / orig_w, max_h / orig_h)
        target_w = max(1, int(round(orig_w * scale)))
        target_h = max(1, int(round(orig_h * scale)))

        return cls(
            original_dimensions=original_dimensions,
            target_dimensions=(target_w, target_h),
            scale_factors=(scale, scale),
            crop_offset=(0, 0),
            crop_size=original_dimensions,
            physical_origin=physical_origin,
        )

    @classmethod
    def from_crop(
        cls,
        original_dimensions: Tuple[int, int],
        crop_rect: Tuple[int, int, int, int],
        target_dimensions: Tuple[int, int] | None = None,
        physical_origin: Tuple[int, int] = (0, 0),
    ) -> CoordinateTransform:
        crop_x, crop_y, crop_w, crop_h = crop_rect
        if crop_w <= 0 or crop_h <= 0:
            raise ValueError(f"Crop width and height must be positive: {crop_rect}")

        if target_dimensions is None:
            target_w, target_h = crop_w, crop_h
            scale_x, scale_y = 1.0, 1.0
        else:
            target_w, target_h = target_dimensions
            scale_x = target_w / crop_w
            scale_y = target_h / crop_h

        return cls(
            original_dimensions=original_dimensions,
            target_dimensions=(target_w, target_h),
            scale_factors=(scale_x, scale_y),
            crop_offset=(crop_x, crop_y),
            crop_size=(crop_w, crop_h),
            physical_origin=physical_origin,
        )

    def model_to_frame_bbox(self, model_box: Sequence[int]) -> list[int]:
        if len(model_box) != 4:
            raise ValueError(f"Expected 4-element box [x, y, w, h], got {model_box}")
        scale_x, scale_y = self.scale_factors
        crop_x, crop_y = self.crop_offset
        return [
            crop_x + int(round(model_box[0] / scale_x)),
            crop_y + int(round(model_box[1] / scale_y)),
            max(1, int(round(model_box[2] / scale_x))),
            max(1, int(round(model_box[3] / scale_y))),
        ]

    def model_to_physical(
        self,
        box_or_point: Union[Sequence[int], Tuple[int, ...]],
    ) -> Tuple[int, ...]:
        scale_x, scale_y = self.scale_factors
        crop_x, crop_y = self.crop_offset
        orig_x, orig_y = self.physical_origin

        if len(box_or_point) == 2:
            mx, my = box_or_point
            px = orig_x + crop_x + int(round(mx / scale_x))
            py = orig_y + crop_y + int(round(my / scale_y))
            return (px, py)
        if len(box_or_point) == 4:
            mx, my, mw, mh = box_or_point
            px = orig_x + crop_x + int(round(mx / scale_x))
            py = orig_y + crop_y + int(round(my / scale_y))
            pw = max(1, int(round(mw / scale_x)))
            ph = max(1, int(round(mh / scale_y)))
            return (px, py, pw, ph)
        raise ValueError(f"Expected 2-point or 4-box sequence, got length {len(box_or_point)}")

    def physical_to_model(
        self,
        box_or_point: Union[Sequence[int], Tuple[int, ...]],
    ) -> Tuple[int, ...]:
        scale_x, scale_y = self.scale_factors
        crop_x, crop_y = self.crop_offset
        orig_x, orig_y = self.physical_origin

        if len(box_or_point) == 2:
            px, py = box_or_point
            rel_x = px - orig_x - crop_x
            rel_y = py - orig_y - crop_y
            mx = int(round(rel_x * scale_x))
            my = int(round(rel_y * scale_y))
            return (mx, my)
        if len(box_or_point) == 4:
            px, py, pw, ph = box_or_point
            rel_x = px - orig_x - crop_x
            rel_y = py - orig_y - crop_y
            mx = int(round(rel_x * scale_x))
            my = int(round(rel_y * scale_y))
            mw = max(1, int(round(pw * scale_x)))
            mh = max(1, int(round(ph * scale_y)))
            return (mx, my, mw, mh)
        raise ValueError(f"Expected 2-point or 4-box sequence, got length {len(box_or_point)}")

    def apply_to_image(self, image: np.ndarray) -> np.ndarray:
        crop_x, crop_y = self.crop_offset
        crop_w, crop_h = self.crop_size
        target_w, target_h = self.target_dimensions

        if self.crop_size != self.original_dimensions or self.crop_offset != (0, 0):
            image = image[crop_y : crop_y + crop_h, crop_x : crop_x + crop_w]

        curr_h, curr_w = image.shape[:2]
        if (curr_w, curr_h) != (target_w, target_h):
            interpolation = cv2.INTER_AREA if (target_w < curr_w or target_h < curr_h) else cv2.INTER_LINEAR
            image = cv2.resize(image, (target_w, target_h), interpolation=interpolation)

        return image
