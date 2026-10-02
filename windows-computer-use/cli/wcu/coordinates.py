"""Coordinate space translation and geometry calculation for WCU.

Supports physical frame pixels, logical points (DIPs based on DPI scale factor),
and normalized coordinates (both 0.0..1.0 unit floats and 0..1000 integers).
"""

from __future__ import annotations

from typing import Any, Dict, Sequence, Tuple


def build_geometry_metadata(
    meta: Dict[str, Any],
    crop_size: Tuple[int, int] | None = None,
) -> Dict[str, Any]:
    """Build standardized geometry dictionary from observation metadata.

    Returns:
        {
            "dpi": int,
            "scale_factor": float,
            "physical_bounds": {"x": int, "y": int, "w": int, "h": int},
            "logical_bounds": {"x": int, "y": int, "w": int, "h": int},
            "image_dimensions": {"w": int, "h": int},
        }
    """
    width = int(meta.get("width", 0))
    height = int(meta.get("height", 0))
    dpi = int(meta.get("dpi") or 96)
    if dpi <= 0:
        dpi = 96
    scale_factor = float(dpi) / 96.0

    cb = meta.get("capture_bounds_physical_px") or {}
    physical_bounds = {
        "x": int(cb.get("x", 0)),
        "y": int(cb.get("y", 0)),
        "w": int(cb.get("w", width)),
        "h": int(cb.get("h", height)),
    }
    logical_bounds = {
        "x": int(round(physical_bounds["x"] / scale_factor)),
        "y": int(round(physical_bounds["y"] / scale_factor)),
        "w": int(round(physical_bounds["w"] / scale_factor)),
        "h": int(round(physical_bounds["h"] / scale_factor)),
    }
    img_w = crop_size[0] if crop_size is not None else width
    img_h = crop_size[1] if crop_size is not None else height
    image_dimensions = {"w": img_w, "h": img_h}

    return {
        "dpi": dpi,
        "scale_factor": scale_factor,
        "physical_bounds": physical_bounds,
        "logical_bounds": logical_bounds,
        "image_dimensions": image_dimensions,
    }


def is_normalized_unit(values: Sequence[float]) -> bool:
    """Detect whether values are within 0.0..1.0 unit range or 0..1000 grid.

    If all values are <= 1.0, treat as unit fractions [0.0, 1.0].
    If any value is > 1.0 (up to 1000), treat as 0..1000 normalized grid.
    """
    return all(v <= 1.0 for v in values)


def resolve_point(
    point: Sequence[float],
    geometry: Dict[str, Any],
    coord_space: str = "physical",
    crop_offset: Sequence[int] = (0, 0),
) -> Tuple[int, int]:
    """Translate (x, y) from coord_space into physical frame pixels."""
    if len(point) != 2:
        raise ValueError(f"Expected 2-element point [x, y], got {point}")
    x, y = float(point[0]), float(point[1])
    off_x, off_y = int(crop_offset[0]), int(crop_offset[1])

    if coord_space == "physical":
        return int(round(x)), int(round(y))

    if coord_space == "logical":
        scale = float(geometry.get("scale_factor", 1.0))
        return int(round(x * scale)) + off_x, int(round(y * scale)) + off_y

    if coord_space == "normalized":
        img_dims = geometry.get("image_dimensions", {})
        img_w = int(img_dims.get("w", 1))
        img_h = int(img_dims.get("h", 1))
        if is_normalized_unit([x, y]):
            norm_x, norm_y = x, y
        else:
            norm_x, norm_y = x / 1000.0, y / 1000.0
        return int(round(norm_x * img_w)) + off_x, int(round(norm_y * img_h)) + off_y

    raise ValueError(
        f"Unknown coord_space '{coord_space}'; expected physical, logical, or normalized"
    )


def resolve_bbox(
    bbox: Sequence[float],
    geometry: Dict[str, Any],
    coord_space: str = "physical",
    crop_offset: Sequence[int] = (0, 0),
) -> Tuple[int, int, int, int]:
    """Translate (x, y, w, h) from coord_space into physical frame pixels."""
    if len(bbox) != 4:
        raise ValueError(f"Expected 4-element bbox [x, y, w, h], got {bbox}")
    x, y, w, h = float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
    off_x, off_y = int(crop_offset[0]), int(crop_offset[1])

    if coord_space == "physical":
        return (
            int(round(x)),
            int(round(y)),
            max(1, int(round(w))),
            max(1, int(round(h))),
        )

    if coord_space == "logical":
        scale = float(geometry.get("scale_factor", 1.0))
        return (
            int(round(x * scale)) + off_x,
            int(round(y * scale)) + off_y,
            max(1, int(round(w * scale))),
            max(1, int(round(h * scale))),
        )

    if coord_space == "normalized":
        img_dims = geometry.get("image_dimensions", {})
        img_w = int(img_dims.get("w", 1))
        img_h = int(img_dims.get("h", 1))
        if is_normalized_unit([x, y, w, h]):
            norm_x, norm_y, norm_w, norm_h = x, y, w, h
        else:
            norm_x, norm_y, norm_w, norm_h = (
                x / 1000.0,
                y / 1000.0,
                w / 1000.0,
                h / 1000.0,
            )
        return (
            int(round(norm_x * img_w)) + off_x,
            int(round(norm_y * img_h)) + off_y,
            max(1, int(round(norm_w * img_w))),
            max(1, int(round(norm_h * img_h))),
        )

    raise ValueError(
        f"Unknown coord_space '{coord_space}'; expected physical, logical, or normalized"
    )
