"""Phase 4 Verification Suite: OCR and Visual Target Recognition without Input.

Verifies:
1. Single initialization of RapidOCR with tracked cold start and warmup timings.
2. Accurate raw BGRA buffer conversion and stride handling.
3. Accurate identification of positive Continue target within configured ROI.
4. Immediate abstention on stop labels ('Must Respond').
5. Immediate abstention on ambiguous multiple Continue targets.
6. Immediate abstention on non-target screens, loading screens, and obscuring dialogs.
7. Immediate rejection on dimension / geometry mismatch.
8. Generation and saving of visual evidence artifacts with bounding boxes in research/annotated_evidence/.
"""

import json
import os
import sys
import time
import unittest
import cv2
import numpy as np

# Add client directory to python path
cur_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(cur_dir, ".."))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from client.recognition import OcrRecognizer, Profile, RecognitionOutcome, TargetBox


class TestPhase4Recognition(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root_dir = root_dir
        cls.fixtures_dir = os.path.join(root_dir, "tests", "fixtures", "labeled_frames")
        cls.manifest_path = os.path.join(cls.fixtures_dir, "manifest.json")
        cls.evidence_dir = os.path.join(root_dir, "research", "annotated_evidence")
        os.makedirs(cls.evidence_dir, exist_ok=True)

        with open(cls.manifest_path, "r", encoding="utf-8") as f:
            cls.manifest = json.load(f)

        # Load profiles
        cls.fm_profile = Profile.from_file(os.path.join(root_dir, "profiles", "fm24.example.json"))
        cls.fixture_profile = Profile.from_file(os.path.join(root_dir, "profiles", "fixture.json"))
        cls.profiles = {
            "football_manager_2024": cls.fm_profile,
            "wcu_fixture_app": cls.fixture_profile,
        }

        # Initialize recognizer
        cls.recognizer = OcrRecognizer.get_instance()
        print(f"\n[Recognizer] Cold start init time: {cls.recognizer.cold_start_ms:.2f} ms")
        warmup_ms = cls.recognizer.warmup()
        print(f"[Recognizer] Warmup inference time: {warmup_ms:.2f} ms")

    def test_01_bgra_buffer_conversion(self):
        """Test decoding raw BGRA bytes with stride padding into BGR array."""
        h, w = 100, 150
        stride_bytes = 160 * 4  # 160 pixels row pitch (10 pixels padding)
        
        # Create deterministic synthetic BGRA buffer
        buf = bytearray(stride_bytes * h)
        for y in range(h):
            for x in range(w):
                offset = y * stride_bytes + x * 4
                buf[offset + 0] = 50   # B
                buf[offset + 1] = 100  # G
                buf[offset + 2] = 150  # R
                buf[offset + 3] = 255  # A

        img = OcrRecognizer.frame_from_bgra_buffer(bytes(buf), w, h, stride_bytes)
        self.assertEqual(img.shape, (h, w, 3))
        # Verify color conversion: Blue=50, Green=100, Red=150
        pixel = img[10, 10]
        self.assertEqual(int(pixel[0]), 50)
        self.assertEqual(int(pixel[1]), 100)
        self.assertEqual(int(pixel[2]), 150)

    def test_02_manifest_recognition_suite(self):
        """Execute recognition against all labeled test frames and verify gating decisions."""
        for filename, spec in self.manifest.items():
            filepath = os.path.join(self.fixtures_dir, filename)
            self.assertTrue(os.path.exists(filepath), f"Missing test frame: {filepath}")

            img = cv2.imread(filepath)
            self.assertIsNotNone(img, f"Failed reading image: {filepath}")

            profile_name = spec["profile"]
            profile = self.profiles[profile_name]
            expected_status = spec["expected_status"]

            outcome = self.recognizer.recognize(img, profile)
            print(f"\n--- Frame: {filename} ({profile.name}) ---")
            print(f"Status: {outcome.status} (expected: {expected_status})")
            print(f"OCR latency: {outcome.ocr_latency_ms:.2f} ms")
            print(f"Reason: {outcome.reason}")

            self.assertEqual(
                outcome.status,
                expected_status,
                f"Frame {filename} failed: expected '{expected_status}', got '{outcome.status}' ({outcome.reason})",
            )

            # Draw visual evidence annotation
            evidence_img = img.copy()
            # Draw ROI box
            rx1 = int(round(profile.target_roi[0] * img.shape[1]))
            ry1 = int(round(profile.target_roi[1] * img.shape[0]))
            rx2 = int(round(profile.target_roi[2] * img.shape[1]))
            ry2 = int(round(profile.target_roi[3] * img.shape[0]))
            cv2.rectangle(evidence_img, (rx1, ry1), (rx2, ry2), (255, 180, 0), 2)  # Blue ROI box

            if outcome.status == "target_found":
                self.assertIsNotNone(outcome.target)
                target = outcome.target
                self.assertEqual(target.label, spec["expected_label"])
                # Annotate target with green box
                x, y, w, h = target.bbox
                cv2.rectangle(evidence_img, (x, y), (x + w, y + h), (0, 255, 0), 3)
                label_text = f"{target.label} ({target.confidence:.2f})"
                cv2.putText(evidence_img, label_text, (x, max(30, y - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)

            elif outcome.status == "stop_label":
                self.assertIsNotNone(outcome.stop_label)
                self.assertEqual(outcome.stop_label, spec["expected_stop_label"])
                # Annotate stop label with red box
                for c in outcome.all_candidates:
                    x, y, w, h = c.bbox
                    cv2.rectangle(evidence_img, (x, y), (x + w, y + h), (0, 0, 255), 3)
                    cv2.putText(evidence_img, f"STOP: {c.label}", (x, max(30, y - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2)

            elif outcome.status == "ambiguous_target":
                # Annotate ambiguous targets with yellow boxes
                for c in outcome.all_candidates:
                    x, y, w, h = c.bbox
                    cv2.rectangle(evidence_img, (x, y), (x + w, y + h), (0, 255, 255), 2)
                    cv2.putText(evidence_img, f"AMBIGUOUS: {c.label}", (x, max(30, y - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)

            # Write annotated evidence file
            evidence_out_path = os.path.join(self.evidence_dir, f"annotated_{filename}")
            cv2.imwrite(evidence_out_path, evidence_img)
            self.assertTrue(os.path.exists(evidence_out_path))

    def test_03_english_only_gating(self):
        """Test that foreign or unsupported language does not trigger false positive click targets."""
        img = np.zeros((1680, 2520, 3), dtype=np.uint8)
        # Put German or French button: 'Weiter' or 'Continuer' in ROI
        cv2.rectangle(img, (2150, 25), (2450, 95), (180, 70, 30), -1)
        cv2.putText(img, "Weiter", (2190, 70), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 255, 255), 2)

        outcome = self.recognizer.recognize(img, self.fm_profile)
        self.assertEqual(outcome.status, "no_target")
        self.assertIsNone(outcome.target)


if __name__ == "__main__":
    unittest.main()
