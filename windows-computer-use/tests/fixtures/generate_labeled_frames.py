"""Generate labeled fixture frames for Phase 4 visual recognition testing.

Generates positive, negative, edge-case, and stop-label frames required by the
implementation plan:
- continue_present (Positive: exactly 1 Continue target in ROI)
- must_respond_present (Negative: stop label in ROI -> must abstain)
- no_target (Negative: no target in ROI -> must abstain)
- duplicated_continue (Negative: ambiguous multiple targets in ROI -> must abstain)
- loading_screen (Negative: loading/splash screen with no target -> must abstain)
- changed_geometry (Negative: dimension mismatch -> must reject)
- obscuring_dialog (Negative: dialog overlay obscuring ROI -> must abstain)
- fixture_continue_present (Positive: real captured fixture frame)
"""

import json
import os
import shutil
import cv2
import numpy as np


def create_fm_base_frame(w=2520, h=1680):
    # Dark FM-like background (dark purple / slate)
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = (35, 20, 25)  # BGR

    # Top header bar (height ~120px)
    cv2.rectangle(img, (0, 0), (w, 120), (55, 30, 40), -1)
    cv2.line(img, (0, 120), (w, 120), (80, 50, 65), 2)

    # In-game date on top left
    cv2.putText(img, "Sat 12 Aug 2023 - 15:00", (40, 75), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (200, 200, 210), 2)
    return img


def draw_button(img, text, x, y, w, h, bg_color=(180, 70, 30), text_color=(255, 255, 255)):
    cv2.rectangle(img, (x, y), (x + w, y + h), bg_color, -1)
    cv2.rectangle(img, (x, y), (x + w, y + h), (220, 120, 60), 2)
    # Center text
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 1.1
    thickness = 2
    (tw, th), _ = cv2.getTextSize(text, font, font_scale, thickness)
    tx = x + (w - tw) // 2
    ty = y + (h + th) // 2
    cv2.putText(img, text, (tx, ty), font, font_scale, text_color, thickness)


def generate():
    out_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "labeled_frames"))
    os.makedirs(out_dir, exist_ok=True)

    manifest = {}

    # 1. FM Continue Present (Positive)
    img_continue = create_fm_base_frame()
    # Button in top-right ROI [0.75, 0.0, 1.0, 0.15]: e.g., x=2150, y=25, w=300, h=70
    draw_button(img_continue, "Continue", 2150, 25, 300, 70, bg_color=(180, 70, 30))
    p_continue = os.path.join(out_dir, "fm_continue_present.png")
    cv2.imwrite(p_continue, img_continue)
    manifest["fm_continue_present.png"] = {
        "profile": "football_manager_2024",
        "expected_status": "target_found",
        "expected_label": "Continue",
        "expected_bbox_approx": [2150, 25, 300, 70],
    }

    # 2. FM Must Respond Present (Stop condition)
    img_respond = create_fm_base_frame()
    draw_button(img_respond, "Must Respond", 2100, 25, 350, 70, bg_color=(30, 30, 180))
    p_respond = os.path.join(out_dir, "fm_must_respond_present.png")
    cv2.imwrite(p_respond, img_respond)
    manifest["fm_must_respond_present.png"] = {
        "profile": "football_manager_2024",
        "expected_status": "stop_label",
        "expected_stop_label": "Must Respond",
    }

    # 3. FM No Target (Negative)
    img_no_target = create_fm_base_frame()
    draw_button(img_no_target, "Calendar", 2200, 25, 250, 70, bg_color=(60, 60, 70))
    p_no_target = os.path.join(out_dir, "fm_no_target.png")
    cv2.imwrite(p_no_target, img_no_target)
    manifest["fm_no_target.png"] = {
        "profile": "football_manager_2024",
        "expected_status": "no_target",
    }

    # 4. FM Duplicated Continue (Negative - Ambiguous)
    img_dup = create_fm_base_frame()
    draw_button(img_dup, "Continue", 1950, 25, 240, 70, bg_color=(180, 70, 30))
    draw_button(img_dup, "Continue", 2230, 25, 240, 70, bg_color=(180, 70, 30))
    p_dup = os.path.join(out_dir, "fm_duplicated_continue.png")
    cv2.imwrite(p_dup, img_dup)
    manifest["fm_duplicated_continue.png"] = {
        "profile": "football_manager_2024",
        "expected_status": "ambiguous_target",
    }

    # 5. Loading Screen (Negative - Real FM intro frame)
    real_loading_src = os.path.abspath(os.path.join(out_dir, "..", "..", "prototypes", "benchmark_frames", "fm_main_menu.png"))
    p_loading = os.path.join(out_dir, "fm_loading_screen.png")
    if os.path.exists(real_loading_src):
        shutil.copyfile(real_loading_src, p_loading)
    else:
        # Fallback dark splash
        img_load = np.zeros((1680, 2520, 3), dtype=np.uint8)
        cv2.putText(img_load, "FOOTBALL MANAGER 2024", (600, 840), cv2.FONT_HERSHEY_SIMPLEX, 3.0, (255, 255, 255), 4)
        cv2.imwrite(p_loading, img_load)
    manifest["fm_loading_screen.png"] = {
        "profile": "football_manager_2024",
        "expected_status": "no_target",
    }

    # 6. Changed Geometry (Negative - Dimension mismatch)
    img_geom = np.zeros((1080, 1920, 3), dtype=np.uint8)
    draw_button(img_geom, "Continue", 1600, 25, 250, 60, bg_color=(180, 70, 30))
    p_geom = os.path.join(out_dir, "fm_changed_geometry.png")
    cv2.imwrite(p_geom, img_geom)
    manifest["fm_changed_geometry.png"] = {
        "profile": "football_manager_2024",
        "expected_status": "geometry_mismatch",
    }

    # 7. Obscuring Dialog (Negative - Target obscured by popup modal)
    img_obscured = create_fm_base_frame()
    draw_button(img_obscured, "Continue", 2150, 25, 300, 70, bg_color=(180, 70, 30))
    # Draw large modal dialog obscuring top right and center
    cv2.rectangle(img_obscured, (1800, 0), (2520, 300), (20, 20, 20), -1)
    cv2.putText(img_obscured, "Saving Game...", (1850, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (150, 150, 150), 2)
    p_obscured = os.path.join(out_dir, "fm_obscuring_dialog.png")
    cv2.imwrite(p_obscured, img_obscured)
    manifest["fm_obscuring_dialog.png"] = {
        "profile": "football_manager_2024",
        "expected_status": "no_target",
    }

    # 8. Fixture Continue Present (Positive - Controlled Fixture App)
    fixture_src = os.path.abspath(os.path.join(out_dir, "..", "..", "..", "research", "evidence_fixture_wgc.png"))
    p_fixture = os.path.join(out_dir, "fixture_continue_present.png")
    if os.path.exists(fixture_src):
        shutil.copyfile(fixture_src, p_fixture)
        manifest["fixture_continue_present.png"] = {
            "profile": "wcu_fixture_app",
            "expected_status": "target_found",
            "expected_label": "Continue",
        }

    # Write manifest
    manifest_path = os.path.join(out_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"Generated {len(manifest)} labeled frames and saved manifest to {manifest_path}")


if __name__ == "__main__":
    generate()
