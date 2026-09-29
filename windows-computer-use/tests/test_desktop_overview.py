"""Desktop and Monitor Overview Tests.

Tests the desktop and monitor overview capability across WcuClient IPC,
monitor enumeration, coordinate transforms, monitor capture, and overview clicks.
"""

from __future__ import annotations

import os
import sys
import unittest

root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
client_dir = os.path.join(root_dir, "client")
if client_dir not in sys.path:
    sys.path.insert(0, client_dir)

from transforms import CoordinateTransform
from wcu_client import WcuClient, WcuError


class TestMonitorEnumeration(unittest.TestCase):
    def test_list_monitors_contract(self):
        """Verify monitor enumeration invariants: primary designation, bounds, and window mapping."""
        with WcuClient() as client:
            monitors = client.list_monitors()
            self.assertGreaterEqual(len(monitors), 1, "Must detect at least one active monitor")

            primary_count = sum(1 for m in monitors if m.get("is_primary"))
            self.assertEqual(primary_count, 1, "Exactly one primary monitor must be designated")

            for mon in monitors:
                self.assertTrue(len(mon.get("device_name", "")) > 0)
                bounds = mon.get("bounds", {})
                self.assertGreater(bounds.get("w", 0), 0)
                self.assertGreater(bounds.get("h", 0), 0)
                self.assertGreaterEqual(mon.get("dpi", 0), 96)
                self.assertGreaterEqual(mon.get("scale_factor", 0.0), 1.0)

            windows = client.list_windows()
            visible_windows = [w for w in windows if w.get("is_visible") and w.get("monitor_handle")]
            self.assertGreater(len(visible_windows), 0, "Visible windows should report their monitor handle")


class TestCoordinateTransform(unittest.TestCase):
    def test_fit_scaling_and_point_inversion(self):
        """Verify downscaled point maps to physical coordinates and inverts without drift."""
        t = CoordinateTransform.from_fit(
            original_dimensions=(3840, 2160),
            max_dimensions=(1920, 1080),
            physical_origin=(0, 0),
        )

        phys = t.model_to_physical((960, 540))
        self.assertEqual(phys, (1920, 1080))

        model_back = t.physical_to_model(phys)
        self.assertEqual(model_back, (960, 540))

        frame_box = t.model_to_frame_bbox([100, 100, 200, 150])
        self.assertEqual(frame_box, [200, 200, 400, 300])

    def test_negative_virtual_desktop_coordinates(self):
        """Test multi-monitor layout where secondary monitor is to the left with negative X."""
        # Secondary monitor at (-1920, 0) of size 1920x1080, scaled to 1280x720
        t = CoordinateTransform.from_fit(
            original_dimensions=(1920, 1080),
            max_dimensions=(1280, 720),
            physical_origin=(-1920, 0),
        )

        self.assertEqual(t.physical_origin, (-1920, 0))
        # Top-left corner (0, 0) in model space maps to (-1920, 0) physical
        self.assertEqual(t.model_to_physical((0, 0)), (-1920, 0))

        # Bottom-right corner (1280, 720) in model space maps to (0, 1080) physical
        self.assertEqual(t.model_to_physical((1280, 720)), (0, 1080))

        # Round-trip check
        pt = (-960, 540)
        model_pt = t.physical_to_model(pt)
        phys_pt = t.model_to_physical(model_pt)
        self.assertEqual(phys_pt, pt)

    def test_bbox_transformation_and_aspect_ratios(self):
        """Test bounding box mapping across ultrawide aspect ratio."""
        # Ultrawide 3440x1440 fitted to max 1920x1080: aspect ratio preserved (fits to width 1920, height 803)
        t = CoordinateTransform.from_fit(
            original_dimensions=(3440, 1440),
            max_dimensions=(1920, 1080),
            physical_origin=(100, 200),
        )

        # Scale factor must be uniform to preserve aspect ratio
        self.assertAlmostEqual(t.scale_factors[0], t.scale_factors[1], places=4)

        # Test bounding box [x, y, w, h]
        bbox_model = (100, 50, 200, 100)
        phys_box = t.model_to_physical(bbox_model)
        self.assertEqual(len(phys_box), 4)

        # Invert back to model space
        inverted_model_box = t.physical_to_model(phys_box)
        # Verify box position and size within 1px rounding tolerance
        for m, inv in zip(bbox_model, inverted_model_box):
            self.assertAlmostEqual(m, inv, delta=1)


class TestMonitorCapture(unittest.TestCase):
    def test_observe_primary_monitor_default(self):
        """Verify capturing the primary monitor without window attachment."""
        with WcuClient() as client:
            meta, payload = client.observe_monitor()

            self.assertIsInstance(meta, dict)
            self.assertEqual(meta.get("target_type"), "monitor")
            self.assertGreater(meta.get("observation_id", 0), 0)
            self.assertGreater(meta.get("frame_id", 0), 0)
            self.assertGreater(meta.get("width", 0), 0)
            self.assertGreater(meta.get("height", 0), 0)
            self.assertEqual(meta.get("pixel_format"), "BGRA8")

            stride = meta.get("stride_bytes", 0)
            width = meta["width"]
            height = meta["height"]
            self.assertGreaterEqual(stride, width * 4)
            self.assertEqual(len(payload), stride * height)

            # Bounds should match primary monitor physical bounds
            bounds = meta.get("capture_bounds_physical_px", {})
            self.assertEqual(bounds.get("w"), width)
            self.assertEqual(bounds.get("h"), height)

    def test_observe_monitor_by_index(self):
        """Verify capturing a monitor explicitly specified by index."""
        with WcuClient() as client:
            monitors = client.list_monitors()
            self.assertTrue(len(monitors) > 0)

            meta, payload = client.observe_monitor(monitor_index=0)
            self.assertEqual(meta.get("target_type"), "monitor")
            self.assertEqual(meta["monitor_info"]["index"], 0)
            self.assertGreater(len(payload), 0)

    def test_observe_via_unified_observe(self):
        """Verify unified client.observe(monitor_index=...) without prior attachment."""
        with WcuClient() as client:
            meta, payload = client.observe(monitor_index=0)
            self.assertEqual(meta.get("target_type"), "monitor")
            self.assertGreater(len(payload), 0)


class TestOverviewClickAndFocus(unittest.TestCase):
    def test_overview_click_dry_run_hit_test(self):
        """Verify overview click with dry_run=True performs hit-testing without injecting input."""
        with WcuClient() as client:
            meta, _ = client.observe_monitor()
            obs_id = meta["observation_id"]
            width = meta["width"]
            height = meta["height"]

            # Click near center of primary monitor
            target_bbox = [width // 2 - 10, height // 2 - 10, 20, 20]
            res = client.click(
                observation_id=obs_id,
                target_bbox_frame_px=target_bbox,
                max_age_ms=2000,
                dry_run=True,
            )

            self.assertEqual(res.get("status"), "dry_run")
            self.assertEqual(res.get("target_type"), "monitor")
            self.assertEqual(res.get("events_injected"), 0)
            self.assertIn("screen_x", res)
            self.assertIn("screen_y", res)

            hit_window = res.get("hit_window", {})
            self.assertIsInstance(hit_window, dict)
            self.assertIn("hwnd", hit_window)
            self.assertIn("root_hwnd", hit_window)
            self.assertIn("title", hit_window)
            self.assertIn("pid", hit_window)

    def test_overview_click_stale_observation_rejection(self):
        """Verify that stale overview observations are rejected before hit-testing or dispatch."""
        import time

        with WcuClient() as client:
            meta, _ = client.observe_monitor()
            obs_id = meta["observation_id"]

            time.sleep(0.15)
            with self.assertRaises(WcuError) as ctx:
                client.click(
                    observation_id=obs_id,
                    target_bbox_frame_px=[10, 10, 20, 20],
                    max_age_ms=50,  # 50ms timeout guarantees rejection
                    dry_run=True,
                )
            self.assertEqual(ctx.exception.code, "stale_observation")

    def test_overview_click_out_of_bounds_rejection(self):
        """Verify that overview click coordinates outside the monitor frame are rejected."""
        with WcuClient() as client:
            meta, _ = client.observe_monitor()
            obs_id = meta["observation_id"]
            width = meta["width"]
            height = meta["height"]

            with self.assertRaises(WcuError) as ctx:
                client.click(
                    observation_id=obs_id,
                    target_bbox_frame_px=[width + 100, height + 100, 20, 20],
                    max_age_ms=2000,
                    dry_run=True,
                )
            self.assertEqual(ctx.exception.code, "invalid_coordinates")

    def test_focus_window_contract(self):
        """Verify that client.focus_window() successfully brings a window to the foreground."""
        with WcuClient() as client:
            windows = client.list_windows()
            visible_windows = [w for w in windows if w.get("is_visible") and len(w.get("title", "")) > 0]
            self.assertTrue(len(visible_windows) > 0, "At least one visible named window expected")

            target = visible_windows[0]
            res = client.focus_window(target["hwnd"])
            self.assertEqual(res.get("status"), "focused")
            self.assertEqual(res.get("hwnd"), target["hwnd"])


if __name__ == "__main__":
    unittest.main()
