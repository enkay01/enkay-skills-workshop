#!/usr/bin/env python3
"""Automated tests for PyAutoGUI CLI laptop control tool."""

import json
import os
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
TOOL_DIR = REPO_ROOT / "tools" / "pyautogui-cli"
SCRIPT_PATH = TOOL_DIR / "laptop_control.py"
BIN_PATH = TOOL_DIR / "bin" / "laptop-control"
UV_BIN = Path("/Users/iannkwocha/.local/bin/uv")
if not UV_BIN.exists():
    UV_BIN = Path(os.environ.get("HOME", "")) / ".local" / "bin" / "uv"


class TestLaptopControlCLI(unittest.TestCase):
    """Test suite for laptop_control.py CLI and binary wrapper."""

    def _run_cli(self, *args) -> subprocess.CompletedProcess:
        """Helper to invoke laptop_control.py using uv."""
        cmd = [str(UV_BIN), "run", str(SCRIPT_PATH)] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT))

    def _run_bin(self, *args) -> subprocess.CompletedProcess:
        """Helper to invoke bin/laptop-control directly."""
        cmd = [str(BIN_PATH)] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT))

    def test_size_command(self):
        """Verify laptop_control.py size returns valid dimensions and scale factor."""
        res = self._run_cli("size", "--json")
        self.assertEqual(res.returncode, 0, f"size failed: {res.stderr}")
        data = json.loads(res.stdout)

        self.assertIn("logical", data)
        self.assertIn("physical", data)
        self.assertIn("scale_factor", data)

        lw, lh = data["logical"]["width"], data["logical"]["height"]
        pw, ph = data["physical"]["width"], data["physical"]["height"]
        scale = data["scale_factor"]

        self.assertGreater(lw, 0)
        self.assertGreater(lh, 0)
        self.assertGreater(pw, 0)
        self.assertGreater(ph, 0)
        self.assertGreaterEqual(scale, 1.0)
        self.assertEqual(pw, int(round(lw * scale)))
        self.assertEqual(ph, int(round(lh * scale)))

    def test_position_command(self):
        """Verify laptop_control.py position returns current coordinates."""
        res = self._run_cli("position", "--json")
        self.assertEqual(res.returncode, 0, f"position failed: {res.stderr}")
        data = json.loads(res.stdout)

        self.assertIn("logical", data)
        self.assertIn("physical", data)
        self.assertIn("scale_factor", data)

        lx, ly = data["logical"]["x"], data["logical"]["y"]
        px, py = data["physical"]["x"], data["physical"]["y"]
        scale = data["scale_factor"]

        self.assertGreaterEqual(lx, 0)
        self.assertGreaterEqual(ly, 0)
        self.assertEqual(px, int(round(lx * scale)))
        self.assertEqual(py, int(round(ly * scale)))

    def test_self_test_command(self):
        """Verify laptop_control.py self-test completes with all checks passing."""
        res = self._run_cli("self-test", "--json")
        self.assertEqual(res.returncode, 0, f"self-test failed: {res.stderr}")
        data = json.loads(res.stdout)

        self.assertTrue(data.get("passed"), f"Checks failed: {data}")
        checks = {c["name"]: c["passed"] for c in data.get("checks", [])}
        self.assertTrue(checks.get("display_metrics"))
        self.assertTrue(checks.get("cursor_position"))
        self.assertTrue(checks.get("template_matching"))
        self.assertTrue(checks.get("key_normalization"))

    def test_wrapper_binary(self):
        """Verify bin/laptop-control wrapper script runs successfully."""
        self.assertTrue(os.access(BIN_PATH, os.X_OK), "bin/laptop-control is not executable")
        res = self._run_bin("size", "--json")
        self.assertEqual(res.returncode, 0, f"wrapper size failed: {res.stderr}")
        data = json.loads(res.stdout)
        self.assertIn("logical", data)

    def test_batch_dry_run(self):
        """Verify batch command validates step sequences in dry-run mode."""
        batch_file = TOOL_DIR / "examples" / "sample_batch.json"
        res = self._run_cli("batch", str(batch_file), "--dry-run")
        self.assertEqual(res.returncode, 0, f"batch dry-run failed: {res.stderr}")
        self.assertIn("Dry run mode", res.stdout)
        self.assertIn("Step 1", res.stdout)

    def test_screenshot_and_locate(self):
        """Verify screenshot capture and template locate with Retina coordinate conversion."""
        temp_shot = "/tmp/test_shot_autoverify.png"
        temp_tpl = "/tmp/test_tpl_autoverify.png"

        try:
            # 1. Take screenshot
            res = self._run_cli("screenshot", "--output", temp_shot, "--json")
            self.assertEqual(res.returncode, 0, f"screenshot failed: {res.stderr}")
            shot_data = json.loads(res.stdout)
            self.assertTrue(os.path.exists(temp_shot))

            # 2. Crop a region using Pillow
            from PIL import Image
            img = Image.open(temp_shot)
            crop_box = (120, 120, 180, 180)
            cropped = img.crop(crop_box)
            cropped.save(temp_tpl)

            # 3. Locate template
            loc_res = self._run_cli("locate", temp_tpl, "--confidence", "0.85", "--json")
            self.assertEqual(loc_res.returncode, 0, f"locate failed: {loc_res.stderr}")
            loc_data = json.loads(loc_res.stdout)

            self.assertTrue(loc_data.get("found"))
            self.assertGreaterEqual(loc_data.get("confidence", 0), 0.85)
            self.assertIn("box", loc_data)
            self.assertIn("center", loc_data)
            self.assertIn("physical_box", loc_data)
            self.assertIn("scale_factor", loc_data)

            # Check that physical coordinates divide by scale factor to logical coordinates
            scale = loc_data["scale_factor"]
            phys_center = loc_data["physical_center"]
            log_center = loc_data["center"]
            expected_x = round(phys_center["x"] / scale, 1)
            expected_y = round(phys_center["y"] / scale, 1)
            self.assertAlmostEqual(log_center["x"], expected_x, delta=1.0)
            self.assertAlmostEqual(log_center["y"], expected_y, delta=1.0)

        finally:
            for p in (temp_shot, temp_tpl):
                if os.path.exists(p):
                    os.remove(p)

    def test_move_and_position(self):
        """Verify move command updates cursor location."""
        pos_res = self._run_cli("position", "--json")
        self.assertEqual(pos_res.returncode, 0)
        orig_pos = json.loads(pos_res.stdout)["logical"]

        target_x = 250.0
        target_y = 250.0
        move_res = self._run_cli("move", str(target_x), str(target_y), "--duration", "0.05", "--json")
        self.assertEqual(move_res.returncode, 0, f"move failed: {move_res.stderr}")

        new_pos_res = self._run_cli("position", "--json")
        new_pos = json.loads(new_pos_res.stdout)["logical"]
        self.assertAlmostEqual(new_pos["x"], target_x, delta=3.0)
        self.assertAlmostEqual(new_pos["y"], target_y, delta=3.0)

        # Restore original cursor position
        self._run_cli("move", str(orig_pos["x"]), str(orig_pos["y"]), "--duration", "0.05")

    def test_open_app_finder(self):
        """Verify open-app command successfully activates Finder."""
        res = self._run_cli("open-app", "Finder", "--wait", "0.1", "--json")
        self.assertEqual(res.returncode, 0, f"open-app failed: {res.stderr}")
        data = json.loads(res.stdout)
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("app"), "Finder")


if __name__ == "__main__":
    unittest.main()
