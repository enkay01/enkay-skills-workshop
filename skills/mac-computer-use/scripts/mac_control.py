#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "opencv-python",
#     "pillow",
#     "pyautogui",
#     "pyobjc-core",
#     "pyobjc-framework-Quartz",
# ]
# ///

"""PyAutoGUI macOS automation CLI with automatic Retina display coordinate scaling."""

import argparse
import datetime
import json
import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image
import pyautogui

# Set default pyautogui safety parameters
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05

KEY_ALIASES = {
    "cmd": "command",
    "super": "command",
    "win": "command",
    "windows": "command",
    "opt": "option",
    "alt": "option",
    "control": "ctrl",
    "ctl": "ctrl",
    "del": "delete",
    "return": "enter",
    "escape": "esc",
    "pgup": "pageup",
    "pgdn": "pagedown",
    "ins": "insert",
}


def normalize_key(key: str) -> str:
    """Normalize user key names to standard PyAutoGUI key identifiers."""
    cleaned = key.strip().lower()
    return KEY_ALIASES.get(cleaned, cleaned)


def get_display_metrics() -> Dict[str, Any]:
    """Return logical points, physical framebuffer pixels, and Retina scale factor."""
    logical_w = 0
    logical_h = 0
    physical_w = 0
    physical_h = 0
    scale = 1.0

    # 1. Query CoreGraphics / Quartz display mode
    try:
        import Quartz

        main_id = Quartz.CGMainDisplayID()
        mode = Quartz.CGDisplayCopyDisplayMode(main_id)
        if mode:
            physical_w = int(Quartz.CGDisplayModeGetPixelWidth(mode))
            physical_h = int(Quartz.CGDisplayModeGetPixelHeight(mode))
            logical_w = int(Quartz.CGDisplayModeGetWidth(mode))
            logical_h = int(Quartz.CGDisplayModeGetHeight(mode))
            if logical_w > 0:
                scale = float(physical_w) / float(logical_w)
    except Exception:
        pass

    # 2. Query AppKit NSScreen backing scale factor as secondary check
    if scale == 1.0 or logical_w == 0:
        try:
            from AppKit import NSScreen

            main_screen = NSScreen.mainScreen()
            if main_screen:
                appkit_scale = float(main_screen.backingScaleFactor())
                frame = main_screen.frame()
                if logical_w == 0:
                    logical_w = int(frame.size.width)
                    logical_h = int(frame.size.height)
                if scale == 1.0 and appkit_scale > 0:
                    scale = appkit_scale
                if physical_w == 0 and logical_w > 0:
                    physical_w = int(round(logical_w * scale))
                    physical_h = int(round(logical_h * scale))
        except Exception:
            pass

    # 3. Fallback to PyAutoGUI screen size
    if logical_w == 0 or logical_h == 0:
        py_w, py_h = pyautogui.size()
        logical_w = int(py_w)
        logical_h = int(py_h)
        if physical_w == 0:
            physical_w = int(round(logical_w * scale))
            physical_h = int(round(logical_h * scale))

    return {
        "logical": {"width": logical_w, "height": logical_h},
        "physical": {"width": physical_w, "height": physical_h},
        "scale_factor": round(scale, 2),
    }


def to_logical_coords(x: float, y: float, is_physical: bool, scale: float) -> Tuple[float, float]:
    """Convert input coordinates to logical points."""
    if is_physical and scale > 0:
        return x / scale, y / scale
    return x, y


def cmd_size(args: argparse.Namespace) -> int:
    """Print screen size in logical points and physical pixels with scale factor."""
    metrics = get_display_metrics()
    if args.json:
        print(json.dumps(metrics, indent=2))
    else:
        print("Screen Size:")
        print(f"  Logical Points:  {metrics['logical']['width']} x {metrics['logical']['height']}")
        print(f"  Physical Pixels: {metrics['physical']['width']} x {metrics['physical']['height']}")
        print(f"  Scale Factor:    {metrics['scale_factor']}x")
    return 0


def cmd_position(args: argparse.Namespace) -> int:
    """Print current mouse cursor position in logical points and physical pixels."""
    metrics = get_display_metrics()
    scale = metrics["scale_factor"]
    pos = pyautogui.position()
    logical_x = int(pos.x)
    logical_y = int(pos.y)
    physical_x = int(round(logical_x * scale))
    physical_y = int(round(logical_y * scale))

    result = {
        "logical": {"x": logical_x, "y": logical_y},
        "physical": {"x": physical_x, "y": physical_y},
        "scale_factor": scale,
    }

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("Mouse Position:")
        print(f"  Logical Points:  x={logical_x}, y={logical_y}")
        print(f"  Physical Pixels: x={physical_x}, y={physical_y}")
        print(f"  Scale Factor:    {scale}x")
    return 0


def cmd_move(args: argparse.Namespace) -> int:
    """Move mouse cursor to coordinates."""
    metrics = get_display_metrics()
    scale = metrics["scale_factor"]
    target_x, target_y = to_logical_coords(args.x, args.y, args.physical, scale)

    pyautogui.moveTo(target_x, target_y, duration=args.duration)
    current = pyautogui.position()

    result = {
        "action": "move",
        "target": {"x": target_x, "y": target_y},
        "current": {"x": int(current.x), "y": int(current.y)},
        "duration": args.duration,
        "scale_factor": scale,
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Moved cursor to logical coordinates ({target_x:.1f}, {target_y:.1f})")
    return 0


def cmd_click(args: argparse.Namespace) -> int:
    """Click at target coordinates."""
    metrics = get_display_metrics()
    scale = metrics["scale_factor"]
    target_x, target_y = to_logical_coords(args.x, args.y, args.physical, scale)

    pyautogui.click(
        x=target_x,
        y=target_y,
        clicks=args.clicks,
        interval=args.interval,
        button=args.button,
    )

    result = {
        "action": "click",
        "coordinates": {"x": target_x, "y": target_y},
        "button": args.button,
        "clicks": args.clicks,
        "interval": args.interval,
        "scale_factor": scale,
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        btn_str = f"{args.button} button"
        clicks_str = f"{args.clicks} time(s)"
        print(f"Clicked {btn_str} at logical coordinates ({target_x:.1f}, {target_y:.1f}) {clicks_str}")
    return 0


def cmd_double_click(args: argparse.Namespace) -> int:
    """Double-click at target coordinates."""
    metrics = get_display_metrics()
    scale = metrics["scale_factor"]
    target_x, target_y = to_logical_coords(args.x, args.y, args.physical, scale)

    pyautogui.doubleClick(
        x=target_x,
        y=target_y,
        interval=args.interval,
        button=args.button,
    )

    result = {
        "action": "double-click",
        "coordinates": {"x": target_x, "y": target_y},
        "button": args.button,
        "interval": args.interval,
        "scale_factor": scale,
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Double-clicked {args.button} button at logical coordinates ({target_x:.1f}, {target_y:.1f})")
    return 0


def cmd_drag(args: argparse.Namespace) -> int:
    """Drag cursor from (x1, y1) to (x2, y2)."""
    metrics = get_display_metrics()
    scale = metrics["scale_factor"]
    start_x, start_y = to_logical_coords(args.x1, args.y1, args.physical, scale)
    end_x, end_y = to_logical_coords(args.x2, args.y2, args.physical, scale)

    pyautogui.moveTo(start_x, start_y)
    pyautogui.dragTo(end_x, end_y, duration=args.duration, button=args.button)

    result = {
        "action": "drag",
        "start": {"x": start_x, "y": start_y},
        "end": {"x": end_x, "y": end_y},
        "button": args.button,
        "duration": args.duration,
        "scale_factor": scale,
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Dragged from ({start_x:.1f}, {start_y:.1f}) to ({end_x:.1f}, {end_y:.1f}) over {args.duration}s")
    return 0


def cmd_scroll(args: argparse.Namespace) -> int:
    """Scroll vertically."""
    metrics = get_display_metrics()
    scale = metrics["scale_factor"]
    move_x = None
    move_y = None
    if args.x is not None and args.y is not None:
        move_x, move_y = to_logical_coords(args.x, args.y, args.physical, scale)
        pyautogui.moveTo(move_x, move_y)

    pyautogui.scroll(args.clicks, x=move_x, y=move_y)

    result = {
        "action": "scroll",
        "clicks": args.clicks,
        "target": {"x": move_x, "y": move_y} if move_x is not None else None,
        "scale_factor": scale,
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        direction = "up" if args.clicks > 0 else "down"
        at_msg = f" at ({move_x:.1f}, {move_y:.1f})" if move_x is not None else ""
        print(f"Scrolled {direction} by {abs(args.clicks)} clicks{at_msg}")
    return 0


def cmd_type(args: argparse.Namespace) -> int:
    """Type text string."""
    if args.paste:
        process = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
        process.communicate(args.text.encode("utf-8"))
        time.sleep(0.05)
        pyautogui.hotkey("command", "v")
    else:
        pyautogui.write(args.text, interval=args.interval)

    result = {
        "action": "type",
        "length": len(args.text),
        "method": "paste" if args.paste else "keystroke",
        "interval": args.interval if not args.paste else 0.0,
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        method_str = "clipboard paste" if args.paste else "typing"
        print(f"Typed text ({len(args.text)} chars) via {method_str}")
    return 0


def cmd_press(args: argparse.Namespace) -> int:
    """Press a single key."""
    norm_key = normalize_key(args.key)
    pyautogui.press(norm_key)

    result = {
        "action": "press",
        "key": norm_key,
        "original_key": args.key,
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Pressed key: {norm_key}")
    return 0


def cmd_hotkey(args: argparse.Namespace) -> int:
    """Execute a key combination."""
    norm_keys = [normalize_key(k) for k in args.keys]
    pyautogui.hotkey(*norm_keys)

    result = {
        "action": "hotkey",
        "keys": norm_keys,
        "original_keys": args.keys,
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        combo = " + ".join(norm_keys)
        print(f"Pressed hotkey combination: {combo}")
    return 0


def cmd_screenshot(args: argparse.Namespace) -> int:
    """Capture screen and save to disk."""
    metrics = get_display_metrics()
    scale = metrics["scale_factor"]

    output_path = args.output
    if not output_path:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = f"screenshot_{timestamp}.png"

    abs_output = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(abs_output), exist_ok=True)

    screenshot = pyautogui.screenshot()
    raw_w, raw_h = screenshot.size

    saved_w, saved_h = raw_w, raw_h
    if args.logical and scale > 1.0:
        target_w = int(round(raw_w / scale))
        target_h = int(round(raw_h / scale))
        screenshot = screenshot.resize((target_w, target_h), Image.Resampling.LANCZOS)
        saved_w, saved_h = target_w, target_h

    screenshot.save(abs_output)

    result = {
        "action": "screenshot",
        "file": abs_output,
        "saved_size": {"width": saved_w, "height": saved_h},
        "framebuffer_size": {"width": raw_w, "height": raw_h},
        "logical_size": metrics["logical"],
        "scale_factor": scale,
        "resized_to_logical": args.logical,
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Screenshot saved to {abs_output}")
        print(f"  Dimensions: {saved_w} x {saved_h} pixels")
        print(f"  Scale Factor: {scale}x")
    return 0


def locate_template(
    template_path: str,
    confidence: float = 0.8,
    display_scale: Optional[float] = None,
) -> Dict[str, Any]:
    """Find template image on screen with automatic Retina scaling."""
    if not os.path.isfile(template_path):
        raise FileNotFoundError(f"Template image file not found: {template_path}")

    template_bgr = cv2.imread(template_path, cv2.IMREAD_COLOR)
    if template_bgr is None:
        raise ValueError(f"Unable to read or decode image file: {template_path}")

    if display_scale is None:
        metrics = get_display_metrics()
        scale = metrics["scale_factor"]
    else:
        scale = display_scale

    screen_pil = pyautogui.screenshot()
    screen_np = np.array(screen_pil)
    screen_bgr = cv2.cvtColor(screen_np, cv2.COLOR_RGB2BGR)

    screen_h, screen_w = screen_bgr.shape[:2]
    orig_tpl_h, orig_tpl_w = template_bgr.shape[:2]

    # Candidate scales to evaluate: native, 2x (if 1x template on Retina), 0.5x (if 2x template on 1x screen)
    candidate_scales = [1.0]
    if scale != 1.0:
        candidate_scales.append(scale)
        if scale > 0:
            candidate_scales.append(1.0 / scale)

    best_match = None
    best_val = -1.0
    best_scale_used = 1.0

    for s in candidate_scales:
        if s == 1.0:
            resized_tpl = template_bgr
        else:
            new_w = int(round(orig_tpl_w * s))
            new_h = int(round(orig_tpl_h * s))
            if new_w <= 0 or new_h <= 0 or new_w > screen_w or new_h > screen_h:
                continue
            interp = cv2.INTER_LINEAR if s > 1.0 else cv2.INTER_AREA
            resized_tpl = cv2.resize(template_bgr, (new_w, new_h), interpolation=interp)

        tpl_h, tpl_w = resized_tpl.shape[:2]
        if tpl_w > screen_w or tpl_h > screen_h:
            continue

        res = cv2.matchTemplate(screen_bgr, resized_tpl, cv2.TM_CCOEFF_NORMED)
        min_v, max_v, min_l, max_l = cv2.minMaxLoc(res)

        if max_v > best_val:
            best_val = float(max_v)
            best_scale_used = s
            best_match = {
                "physical_x": int(max_l[0]),
                "physical_y": int(max_l[1]),
                "physical_width": int(tpl_w),
                "physical_height": int(tpl_h),
            }

    if best_val < confidence or best_match is None:
        return {
            "found": False,
            "confidence": round(best_val, 4) if best_val >= 0 else 0.0,
            "threshold": confidence,
            "scale_factor": scale,
        }

    phys_x = best_match["physical_x"]
    phys_y = best_match["physical_y"]
    phys_w = best_match["physical_width"]
    phys_h = best_match["physical_height"]

    logical_x = round(phys_x / scale, 1)
    logical_y = round(phys_y / scale, 1)
    logical_w = round(phys_w / scale, 1)
    logical_h = round(phys_h / scale, 1)

    logical_center_x = round(logical_x + logical_w / 2.0, 1)
    logical_center_y = round(logical_y + logical_h / 2.0, 1)

    return {
        "found": True,
        "confidence": round(best_val, 4),
        "threshold": confidence,
        "box": {
            "x": logical_x,
            "y": logical_y,
            "width": logical_w,
            "height": logical_h,
        },
        "center": {
            "x": logical_center_x,
            "y": logical_center_y,
        },
        "physical_box": {
            "x": phys_x,
            "y": phys_y,
            "width": phys_w,
            "height": phys_h,
        },
        "physical_center": {
            "x": int(phys_x + phys_w // 2),
            "y": int(phys_y + phys_h // 2),
        },
        "scale_factor": scale,
        "template_scale_applied": best_scale_used,
    }


def cmd_locate(args: argparse.Namespace) -> int:
    """Locate template image on screen and print coordinates."""
    result = locate_template(args.template, confidence=args.confidence)

    if not result["found"]:
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Template not found (highest confidence: {result['confidence']:.4f} < threshold {args.confidence:.2f})")
        return 1

    if getattr(args, "click", False):
        cx = result["center"]["x"]
        cy = result["center"]["y"]
        pyautogui.click(cx, cy)
        result["clicked"] = True

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        box = result["box"]
        center = result["center"]
        print("Template Located:")
        print(f"  Confidence:     {result['confidence']:.4f} (threshold: {args.confidence:.2f})")
        print(f"  Logical Box:    x={box['x']}, y={box['y']}, width={box['width']}, height={box['height']}")
        print(f"  Logical Center: x={center['x']}, y={center['y']}")
        print(f"  Scale Factor:   {result['scale_factor']}x")
        if getattr(args, "click", False):
            print(f"  Action:         Clicked at ({center['x']}, {center['y']})")
    return 0


def open_mac_application(app_name: str, wait_seconds: float = 0.5) -> Dict[str, Any]:
    """Launch or bring macOS application to front using open -a and AppleScript."""
    # 1. Execute open -a
    open_cmd = ["open", "-a", app_name]
    open_proc = subprocess.run(open_cmd, capture_output=True, text=True)

    # 2. Execute AppleScript activate
    clean_name = app_name[:-4] if app_name.endswith(".app") else app_name
    applescript = f'tell application "{clean_name}" to activate'
    as_proc = subprocess.run(["osascript", "-e", applescript], capture_output=True, text=True)

    if wait_seconds > 0:
        time.sleep(wait_seconds)

    success = (open_proc.returncode == 0) or (as_proc.returncode == 0)
    return {
        "action": "open-app",
        "app": app_name,
        "success": success,
        "open_returncode": open_proc.returncode,
        "open_stderr": open_proc.stderr.strip(),
        "activate_returncode": as_proc.returncode,
        "activate_stderr": as_proc.stderr.strip(),
    }


def cmd_open_app(args: argparse.Namespace) -> int:
    """Launch or activate macOS application."""
    res = open_mac_application(args.app_name, wait_seconds=args.wait)

    if args.json:
        print(json.dumps(res, indent=2))
    else:
        if res["success"]:
            print(f"Application '{args.app_name}' launched and activated")
        else:
            err = res["open_stderr"] or res["activate_stderr"]
            print(f"Failed to launch application '{args.app_name}': {err}", file=sys.stderr)
            return 1
    return 0 if res["success"] else 1


def execute_step(step: Dict[str, Any], default_delay: float) -> Dict[str, Any]:
    """Execute a single step from a batch script."""
    action = step.get("action") or step.get("command")
    if not action:
        raise ValueError(f"Step missing 'action' field: {step}")

    metrics = get_display_metrics()
    scale = metrics["scale_factor"]
    step_result: Dict[str, Any] = {"action": action, "status": "ok"}

    if action == "move":
        x = float(step["x"])
        y = float(step["y"])
        is_phys = bool(step.get("physical", False))
        duration = float(step.get("duration", 0.0))
        tx, ty = to_logical_coords(x, y, is_phys, scale)
        pyautogui.moveTo(tx, ty, duration=duration)
        step_result["target"] = {"x": tx, "y": ty}

    elif action == "click":
        x = float(step["x"])
        y = float(step["y"])
        is_phys = bool(step.get("physical", False))
        button = step.get("button", "left")
        clicks = int(step.get("clicks", 1))
        interval = float(step.get("interval", 0.1))
        tx, ty = to_logical_coords(x, y, is_phys, scale)
        pyautogui.click(x=tx, y=ty, button=button, clicks=clicks, interval=interval)
        step_result["coordinates"] = {"x": tx, "y": ty}

    elif action in ("double-click", "double_click"):
        x = float(step["x"])
        y = float(step["y"])
        is_phys = bool(step.get("physical", False))
        button = step.get("button", "left")
        interval = float(step.get("interval", 0.1))
        tx, ty = to_logical_coords(x, y, is_phys, scale)
        pyautogui.doubleClick(x=tx, y=ty, button=button, interval=interval)
        step_result["coordinates"] = {"x": tx, "y": ty}

    elif action == "drag":
        x1 = float(step["x1"])
        y1 = float(step["y1"])
        x2 = float(step["x2"])
        y2 = float(step["y2"])
        is_phys = bool(step.get("physical", False))
        duration = float(step.get("duration", 0.5))
        button = step.get("button", "left")
        sx, sy = to_logical_coords(x1, y1, is_phys, scale)
        ex, ey = to_logical_coords(x2, y2, is_phys, scale)
        pyautogui.moveTo(sx, sy)
        pyautogui.dragTo(ex, ey, duration=duration, button=button)
        step_result["start"] = {"x": sx, "y": sy}
        step_result["end"] = {"x": ex, "y": ey}

    elif action == "scroll":
        clicks = int(step["clicks"])
        is_phys = bool(step.get("physical", False))
        mx = None
        my = None
        if "x" in step and "y" in step:
            mx, my = to_logical_coords(float(step["x"]), float(step["y"]), is_phys, scale)
            pyautogui.moveTo(mx, my)
        pyautogui.scroll(clicks, x=mx, y=my)
        step_result["clicks"] = clicks

    elif action == "type":
        text = str(step["text"])
        interval = float(step.get("interval", 0.02))
        paste = bool(step.get("paste", False))
        if paste:
            process = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
            process.communicate(text.encode("utf-8"))
            time.sleep(0.05)
            pyautogui.hotkey("command", "v")
        else:
            pyautogui.write(text, interval=interval)
        step_result["length"] = len(text)

    elif action == "press":
        key = normalize_key(str(step["key"]))
        pyautogui.press(key)
        step_result["key"] = key

    elif action == "hotkey":
        keys_list = step["keys"]
        if isinstance(keys_list, str):
            keys_list = keys_list.split()
        norm = [normalize_key(str(k)) for k in keys_list]
        pyautogui.hotkey(*norm)
        step_result["keys"] = norm

    elif action == "screenshot":
        out_path = step.get("output") or f"screenshot_{int(time.time())}.png"
        logical_resize = bool(step.get("logical", False))
        abs_p = os.path.abspath(out_path)
        os.makedirs(os.path.dirname(abs_p), exist_ok=True)
        img = pyautogui.screenshot()
        if logical_resize and scale > 1.0:
            target_w = int(round(img.width / scale))
            target_h = int(round(img.height / scale))
            img = img.resize((target_w, target_h), Image.Resampling.LANCZOS)
        img.save(abs_p)
        step_result["output"] = abs_p

    elif action == "locate":
        tpl_path = step["template"]
        conf = float(step.get("confidence", 0.8))
        auto_click = bool(step.get("click", False))
        loc_res = locate_template(tpl_path, confidence=conf)
        step_result.update(loc_res)
        if loc_res["found"] and auto_click:
            cx = loc_res["center"]["x"]
            cy = loc_res["center"]["y"]
            pyautogui.click(cx, cy)
            step_result["clicked"] = True

    elif action in ("open-app", "open_app"):
        app = step.get("app") or step.get("app_name")
        wait_s = float(step.get("wait", 0.5))
        app_res = open_mac_application(app, wait_seconds=wait_s)
        step_result.update(app_res)

    elif action in ("pause", "sleep"):
        dur = float(step.get("duration", default_delay))
        time.sleep(dur)
        step_result["duration"] = dur

    else:
        raise ValueError(f"Unknown action: {action}")

    # Process step-specific delay or default delay
    post_delay = float(step.get("pause", step.get("delay", default_delay)))
    if post_delay > 0 and action not in ("pause", "sleep"):
        time.sleep(post_delay)
        step_result["post_delay"] = post_delay

    return step_result


def cmd_batch(args: argparse.Namespace) -> int:
    """Execute action steps sequentially from JSON file."""
    if not os.path.isfile(args.steps_file):
        print(f"Error: Steps file not found: {args.steps_file}", file=sys.stderr)
        return 1

    with open(args.steps_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict) and "steps" in data:
        steps = data["steps"]
    elif isinstance(data, list):
        steps = data
    else:
        print("Error: JSON must be an array of steps or contain a 'steps' array.", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"Dry run mode: Validated {len(steps)} steps in {args.steps_file}.")
        for i, s in enumerate(steps, start=1):
            act = s.get("action") or s.get("command") or "unknown"
            print(f"  Step {i}: {act} -> {s}")
        return 0

    results = []
    has_error = False

    for idx, step in enumerate(steps, start=1):
        try:
            res = execute_step(step, default_delay=args.delay)
            res["step_index"] = idx
            results.append(res)
            if not args.json:
                print(f"Step {idx}/{len(steps)}: {res['action']} completed")
        except Exception as exc:
            err_entry = {
                "step_index": idx,
                "action": step.get("action", "unknown"),
                "status": "error",
                "error": str(exc),
            }
            results.append(err_entry)
            print(f"Step {idx}/{len(steps)} failed: {exc}", file=sys.stderr)
            has_error = True
            if args.stop_on_error:
                break

    if args.json:
        print(json.dumps({"results": results, "success": not has_error}, indent=2))

    return 1 if has_error else 0


def cmd_self_test(args: argparse.Namespace) -> int:
    """Run self-diagnostics verifying display metrics, cursor coordinates, and template matching."""
    checks = []

    # Check 1: Display metrics
    try:
        metrics = get_display_metrics()
        lw = metrics["logical"]["width"]
        lh = metrics["logical"]["height"]
        pw = metrics["physical"]["width"]
        ph = metrics["physical"]["height"]
        scale = metrics["scale_factor"]
        checks.append({
            "name": "display_metrics",
            "passed": lw > 0 and lh > 0 and pw > 0 and ph > 0 and scale >= 1.0,
            "details": f"Logical: {lw}x{lh}, Physical: {pw}x{ph}, Scale: {scale}x",
        })
    except Exception as e:
        checks.append({"name": "display_metrics", "passed": False, "details": str(e)})

    # Check 2: Cursor position
    try:
        pos = pyautogui.position()
        checks.append({
            "name": "cursor_position",
            "passed": pos.x >= 0 and pos.y >= 0,
            "details": f"Current cursor: x={pos.x}, y={pos.y}",
        })
    except Exception as e:
        checks.append({"name": "cursor_position", "passed": False, "details": str(e)})

    # Check 3: In-memory screenshot and template matching
    try:
        shot = pyautogui.screenshot()
        shot_w, shot_h = shot.size
        # Crop small region from the screenshot
        crop_box = (100, 100, 160, 160)
        cropped = shot.crop(crop_box)
        temp_tpl = "/tmp/pyautogui_cli_selftest_tpl.png"
        cropped.save(temp_tpl)

        try:
            loc = locate_template(temp_tpl, confidence=0.85)
            match_ok = loc["found"] and loc["confidence"] >= 0.85
            checks.append({
                "name": "template_matching",
                "passed": match_ok,
                "details": f"Match confidence: {loc.get('confidence')}, Center: {loc.get('center')}",
            })
        finally:
            if os.path.exists(temp_tpl):
                os.remove(temp_tpl)
    except Exception as e:
        checks.append({"name": "template_matching", "passed": False, "details": str(e)})

    # Check 4: Key normalization
    try:
        norm_cmd = normalize_key("cmd")
        norm_esc = normalize_key("escape")
        norm_ret = normalize_key("return")
        keys_ok = (norm_cmd == "command") and (norm_esc == "esc") and (norm_ret == "enter")
        checks.append({
            "name": "key_normalization",
            "passed": keys_ok,
            "details": "Normalized cmd -> command, escape -> esc, return -> enter",
        })
    except Exception as e:
        checks.append({"name": "key_normalization", "passed": False, "details": str(e)})

    all_passed = all(c["passed"] for c in checks)
    report = {"passed": all_passed, "checks": checks}

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("Self-Test Diagnostics:")
        for c in checks:
            status = "PASS" if c["passed"] else "FAIL"
            print(f"  [{status}] {c['name']}: {c['details']}")
        print(f"Overall status: {'ALL TESTS PASSED' if all_passed else 'SOME TESTS FAILED'}")

    return 0 if all_passed else 1


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    common_parent = argparse.ArgumentParser(add_help=False)
    common_parent.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Format output as JSON.")

    parser = argparse.ArgumentParser(
        prog="laptop_control.py",
        description="PyAutoGUI macOS automation tool with Retina display support.",
        parents=[common_parent],
    )

    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # size
    p_size = subparsers.add_parser("size", parents=[common_parent], help="Return screen size in logical and physical dimensions.")
    p_size.set_defaults(func=cmd_size)

    # position
    p_pos = subparsers.add_parser("position", parents=[common_parent], help="Return current mouse cursor position.")
    p_pos.set_defaults(func=cmd_position)

    # move <x> <y> [--duration <sec>] [--physical]
    p_move = subparsers.add_parser("move", parents=[common_parent], help="Move mouse cursor to coordinates.")
    p_move.add_argument("x", type=float, help="X coordinate.")
    p_move.add_argument("y", type=float, help="Y coordinate.")
    p_move.add_argument("--duration", type=float, default=0.0, help="Move duration in seconds.")
    p_move.add_argument("--physical", action="store_true", help="Input coordinates are physical pixels.")
    p_move.set_defaults(func=cmd_move)

    # click <x> <y> [--button left|right|middle] [--clicks <n>] [--interval <sec>] [--physical]
    p_click = subparsers.add_parser("click", parents=[common_parent], help="Click at target coordinates.")
    p_click.add_argument("x", type=float, help="X coordinate.")
    p_click.add_argument("y", type=float, help="Y coordinate.")
    p_click.add_argument("--button", choices=["left", "right", "middle"], default="left", help="Mouse button.")
    p_click.add_argument("--clicks", type=int, default=1, help="Number of clicks.")
    p_click.add_argument("--interval", type=float, default=0.1, help="Interval between clicks in seconds.")
    p_click.add_argument("--physical", action="store_true", help="Input coordinates are physical pixels.")
    p_click.set_defaults(func=cmd_click)

    # double-click <x> <y>
    p_dbl = subparsers.add_parser("double-click", aliases=["double_click"], parents=[common_parent], help="Double click at coordinates.")
    p_dbl.add_argument("x", type=float, help="X coordinate.")
    p_dbl.add_argument("y", type=float, help="Y coordinate.")
    p_dbl.add_argument("--button", choices=["left", "right", "middle"], default="left", help="Mouse button.")
    p_dbl.add_argument("--interval", type=float, default=0.1, help="Interval between clicks in seconds.")
    p_dbl.add_argument("--physical", action="store_true", help="Input coordinates are physical pixels.")
    p_dbl.set_defaults(func=cmd_double_click)

    # drag <x1> <y1> <x2> <y2> [--duration <sec>]
    p_drag = subparsers.add_parser("drag", parents=[common_parent], help="Drag cursor from (x1, y1) to (x2, y2).")
    p_drag.add_argument("x1", type=float, help="Starting X coordinate.")
    p_drag.add_argument("y1", type=float, help="Starting Y coordinate.")
    p_drag.add_argument("x2", type=float, help="Ending X coordinate.")
    p_drag.add_argument("y2", type=float, help="Ending Y coordinate.")
    p_drag.add_argument("--duration", type=float, default=0.5, help="Drag duration in seconds.")
    p_drag.add_argument("--button", choices=["left", "right", "middle"], default="left", help="Mouse button.")
    p_drag.add_argument("--physical", action="store_true", help="Input coordinates are physical pixels.")
    p_drag.set_defaults(func=cmd_drag)

    # scroll <clicks> [--x <x>] [--y <y>]
    p_scroll = subparsers.add_parser("scroll", parents=[common_parent], help="Scroll vertically.")
    p_scroll.add_argument("clicks", type=int, help="Scroll clicks (positive=up, negative=down).")
    p_scroll.add_argument("--x", type=float, default=None, help="Target X coordinate before scrolling.")
    p_scroll.add_argument("--y", type=float, default=None, help="Target Y coordinate before scrolling.")
    p_scroll.add_argument("--physical", action="store_true", help="Input coordinates are physical pixels.")
    p_scroll.set_defaults(func=cmd_scroll)

    # type <text> [--interval <sec>] [--paste]
    p_type = subparsers.add_parser("type", parents=[common_parent], help="Type specified text string.")
    p_type.add_argument("text", help="Text string to type.")
    p_type.add_argument("--interval", type=float, default=0.02, help="Interval between keystrokes.")
    p_type.add_argument("--paste", action="store_true", help="Paste text via clipboard.")
    p_type.set_defaults(func=cmd_type)

    # press <key>
    p_press = subparsers.add_parser("press", parents=[common_parent], help="Press a key (e.g. enter, esc, backspace).")
    p_press.add_argument("key", help="Key name to press.")
    p_press.set_defaults(func=cmd_press)

    # hotkey <keys...>
    p_hotkey = subparsers.add_parser("hotkey", parents=[common_parent], help="Execute key combination (e.g. command a).")
    p_hotkey.add_argument("keys", nargs="+", help="Keys in the combination.")
    p_hotkey.set_defaults(func=cmd_hotkey)

    # screenshot [--output <path>] [--logical]
    p_shot = subparsers.add_parser("screenshot", parents=[common_parent], help="Capture screen and save to disk.")
    p_shot.add_argument("--output", default=None, help="Destination file path.")
    p_shot.add_argument("--logical", action="store_true", help="Resize image to logical display points.")
    p_shot.set_defaults(func=cmd_screenshot)

    # locate <template_image> [--confidence <0.0-1.0>]
    p_locate = subparsers.add_parser("locate", parents=[common_parent], help="Locate template image on screen.")
    p_locate.add_argument("template", help="Path to template image file.")
    p_locate.add_argument("--confidence", type=float, default=0.8, help="Match confidence threshold (0.0 to 1.0).")
    p_locate.add_argument("--click", action="store_true", help="Click center coordinate if located.")
    p_locate.set_defaults(func=cmd_locate)

    # open-app <app_name>
    p_open = subparsers.add_parser("open-app", aliases=["open_app"], parents=[common_parent], help="Launch or activate macOS application.")
    p_open.add_argument("app_name", help="Application name (e.g. Safari, Notes, Finder).")
    p_open.add_argument("--wait", type=float, default=0.5, help="Seconds to wait after launch.")
    p_open.set_defaults(func=cmd_open_app)

    # batch <steps_file.json>
    p_batch = subparsers.add_parser("batch", parents=[common_parent], help="Execute action steps sequentially from JSON file.")
    p_batch.add_argument("steps_file", help="Path to JSON steps file.")
    p_batch.add_argument("--delay", type=float, default=0.2, help="Default delay between steps.")
    p_batch.add_argument("--dry-run", action="store_true", help="Validate steps without executing.")
    p_batch.add_argument("--stop-on-error", action="store_true", default=True, help="Stop execution on step failure.")
    p_batch.set_defaults(func=cmd_batch)

    # self-test
    p_test = subparsers.add_parser("self-test", aliases=["selftest"], parents=[common_parent], help="Run self-diagnostic checks.")
    p_test.set_defaults(func=cmd_self_test)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.json = getattr(args, "json", False)

    if not args.command:
        parser.print_help()
        return 0

    if hasattr(args, "func"):
        try:
            return args.func(args)
        except Exception as err:
            if getattr(args, "json", False):
                print(json.dumps({"error": str(err)}, indent=2))
            else:
                print(f"Error: {err}", file=sys.stderr)
            return 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
