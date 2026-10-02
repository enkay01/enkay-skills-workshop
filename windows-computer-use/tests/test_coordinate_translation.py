"""Unit tests for coordinate translation and geometry calculation.

Verifies physical, logical (DPI scaling), and normalized (0..1 and 0..1000)
coordinate mappings for points and bounding boxes.
"""

from __future__ import annotations

import os
import sys
import pytest

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_REPO, "cli"))

from wcu.coordinates import (
    build_geometry_metadata,
    is_normalized_unit,
    resolve_bbox,
    resolve_point,
)


def test_build_geometry_metadata_100_percent():
    meta = {
        "width": 1920,
        "height": 1080,
        "dpi": 96,
        "capture_bounds_physical_px": {"x": 100, "y": 200, "w": 1920, "h": 1080},
    }
    geom = build_geometry_metadata(meta)
    assert geom["dpi"] == 96
    assert geom["scale_factor"] == 1.0
    assert geom["physical_bounds"] == {"x": 100, "y": 200, "w": 1920, "h": 1080}
    assert geom["logical_bounds"] == {"x": 100, "y": 200, "w": 1920, "h": 1080}
    assert geom["image_dimensions"] == {"w": 1920, "h": 1080}


def test_build_geometry_metadata_150_percent():
    meta = {
        "width": 2520,
        "height": 1680,
        "dpi": 144,
        "capture_bounds_physical_px": {"x": 0, "y": 0, "w": 2520, "h": 1680},
    }
    geom = build_geometry_metadata(meta)
    assert geom["dpi"] == 144
    assert geom["scale_factor"] == 1.5
    assert geom["physical_bounds"] == {"x": 0, "y": 0, "w": 2520, "h": 1680}
    assert geom["logical_bounds"] == {"x": 0, "y": 0, "w": 1680, "h": 1120}
    assert geom["image_dimensions"] == {"w": 2520, "h": 1680}


def test_build_geometry_metadata_with_crop():
    meta = {
        "width": 2520,
        "height": 1680,
        "dpi": 144,
        "capture_bounds_physical_px": {"x": 0, "y": 0, "w": 2520, "h": 1680},
    }
    geom = build_geometry_metadata(meta, crop_size=(800, 600))
    assert geom["image_dimensions"] == {"w": 800, "h": 600}


def test_resolve_point_physical():
    geom = {"scale_factor": 1.5, "image_dimensions": {"w": 2520, "h": 1680}}
    pt = resolve_point([1253, 915], geom, coord_space="physical")
    assert pt == (1253, 915)


def test_resolve_point_logical():
    geom = {"scale_factor": 1.5, "image_dimensions": {"w": 2520, "h": 1680}}
    # Logical (1000, 500) at 1.5x scale -> (1500, 750)
    pt = resolve_point([1000, 500], geom, coord_space="logical")
    assert pt == (1500, 750)


def test_resolve_point_logical_with_crop_offset():
    geom = {"scale_factor": 1.5, "image_dimensions": {"w": 1000, "h": 1000}}
    # Logical (100, 100) at 1.5x scale with crop_offset (200, 300) -> (150 + 200, 150 + 300) = (350, 450)
    pt = resolve_point([100, 100], geom, coord_space="logical", crop_offset=(200, 300))
    assert pt == (350, 450)


def test_resolve_point_normalized_unit_floats():
    geom = {"scale_factor": 1.5, "image_dimensions": {"w": 2520, "h": 1680}}
    # Center (0.5, 0.5) -> (1260, 840)
    pt = resolve_point([0.5, 0.5], geom, coord_space="normalized")
    assert pt == (1260, 840)

    # Top left (0.0, 0.0) -> (0, 0)
    assert resolve_point([0.0, 0.0], geom, coord_space="normalized") == (0, 0)
    # Bottom right (1.0, 1.0) -> (2520, 1680)
    assert resolve_point([1.0, 1.0], geom, coord_space="normalized") == (2520, 1680)


def test_resolve_point_normalized_1000_grid():
    geom = {"scale_factor": 1.5, "image_dimensions": {"w": 2520, "h": 1680}}
    # Center (500, 500) -> (1260, 840)
    pt = resolve_point([500, 500], geom, coord_space="normalized")
    assert pt == (1260, 840)

    # (250, 750) -> (630, 1260)
    assert resolve_point([250, 750], geom, coord_space="normalized") == (630, 1260)
    # (1000, 1000) -> (2520, 1680)
    assert resolve_point([1000, 1000], geom, coord_space="normalized") == (2520, 1680)


def test_resolve_bbox_physical():
    geom = {"scale_factor": 2.0, "image_dimensions": {"w": 2000, "h": 1000}}
    bbox = resolve_bbox([100, 200, 300, 400], geom, coord_space="physical")
    assert bbox == (100, 200, 300, 400)


def test_resolve_bbox_logical():
    geom = {"scale_factor": 1.5, "image_dimensions": {"w": 2520, "h": 1680}}
    bbox = resolve_bbox([100, 200, 50, 60], geom, coord_space="logical")
    assert bbox == (150, 300, 75, 90)


def test_resolve_bbox_normalized_unit():
    geom = {"scale_factor": 1.0, "image_dimensions": {"w": 1000, "h": 500}}
    bbox = resolve_bbox([0.1, 0.2, 0.3, 0.4], geom, coord_space="normalized")
    assert bbox == (100, 100, 300, 200)


def test_resolve_bbox_normalized_1000_grid():
    geom = {"scale_factor": 1.0, "image_dimensions": {"w": 1000, "h": 500}}
    bbox = resolve_bbox([100, 200, 300, 400], geom, coord_space="normalized")
    assert bbox == (100, 100, 300, 200)


def test_unknown_coord_space_raises():
    geom = {"scale_factor": 1.0, "image_dimensions": {"w": 1000, "h": 500}}
    with pytest.raises(ValueError, match="Unknown coord_space"):
        resolve_point([10, 10], geom, coord_space="virtual")

    with pytest.raises(ValueError, match="Unknown coord_space"):
        resolve_bbox([10, 10, 20, 20], geom, coord_space="virtual")
