"""Measure per-action dispatch latency. Measurement only, not a pass/fail gate."""

import json
import os
import statistics
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.getcwd(), "client"))
from wcu_client import WcuClient  # noqa: E402

import ctypes  # noqa: E402

user32 = ctypes.windll.user32
h = user32.OpenInputDesktop(0, False, 0x01FF)
if h:
    user32.SetThreadDesktop(h)

exe = os.path.join("tests", "fixture_app", "bin", "Debug", "net6.0-windows", "fixture_app.exe")
proc = subprocess.Popen([exe, "--actions"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
client = WcuClient()
client.start()


def find(title):
    for _ in range(80):
        for w in client.list_windows():
            if title in w.get("title", ""):
                return w
        time.sleep(0.25)
    raise SystemExit("not found: " + title)


win = find("WCU_Native_Action_Fixture")
client.attach(win["hwnd"], win["pid"], win["process_create_time_utc"])
time.sleep(0.6)


def observe():
    meta, _ = client.observe(timeout_ms=2000)
    return meta


def frame_box(el, meta):
    cb = meta["capture_bounds_physical_px"]
    sx, sy = meta["width"] / cb["w"], meta["height"] / cb["h"]
    return [
        int((el["bounds"]["x"] - cb["x"]) * sx),
        int((el["bounds"]["y"] - cb["y"]) * sy),
        int(el["bounds"]["w"] * sx),
        int(el["bounds"]["h"] * sy),
    ]


def by_id(aid):
    els, _ = client.inspect(max_depth=8, max_elements=200)
    return next(e for e in els if e.get("automation_id") == aid)


def timed(label, setup, action, reps=15):
    """Time `action` alone. `setup` runs outside the timed region.

    Keeping setup out of the measurement is what makes the numbers attributable:
    a targeted action's cost is its own guard work, not the observation and
    element lookup a caller would have done anyway.
    """
    samples = []
    for _ in range(reps):
        prepared = setup()
        t0 = time.perf_counter()
        action(prepared)
        samples.append((time.perf_counter() - t0) * 1000.0)
        time.sleep(0.12)
    samples.sort()
    print(
        f"{label:22} n={reps}  median {statistics.median(samples):6.2f} ms  "
        f"min {samples[0]:6.2f}  p95 {samples[int(len(samples) * 0.95) - 1]:6.2f}  "
        f"max {samples[-1]:6.2f}"
    )
    return statistics.median(samples)


results = {}


def nothing():
    return None


results["press_key"] = timed("press_key", nothing, lambda _: client.press_key("shift"))
results["type_text(5)"] = timed("type_text(5)", nothing, lambda _: client.type_text("abcde"))
results["focus_window"] = timed(
    "focus_window", nothing,
    lambda _: client.focus_window(win["hwnd"], pid=win["pid"],
                                  process_create_time_utc=win["process_create_time_utc"]),
)
results["observe"] = timed("observe", nothing, lambda _: observe())


def observe_and_box(aid):
    meta = observe()
    els, _ = client.inspect(max_depth=8, max_elements=200)
    el = next(e for e in els if e.get("automation_id") == aid)
    return meta["observation_id"], frame_box(el, meta)


results["click(targeted)"] = timed(
    "click(targeted)", lambda: observe_and_box("pnl_click"),
    lambda p: client.click(observation_id=p[0], target_bbox_frame_px=p[1], max_age_ms=3000),
)
results["hover(targeted)"] = timed(
    "hover(targeted)", lambda: observe_and_box("pnl_hover"),
    lambda p: client.hover(observation_id=p[0],
                           target=client.target(bbox_frame_px=p[1]), max_age_ms=3000),
)
results["scroll(targeted)"] = timed(
    "scroll(targeted)", lambda: observe_and_box("pnl_scroll"),
    lambda p: client.scroll(notches_x=1, observation_id=p[0], max_age_ms=3000,
                            target=client.target(bbox_frame_px=p[1])),
)


def observe_only():
    return observe()["observation_id"]


results["scroll(untargeted)"] = timed(
    "scroll(untargeted)", observe_only,
    lambda obs: client.scroll(notches_y=-1, observation_id=obs, max_age_ms=3000),
)


def observe_from():
    meta = observe()
    els, _ = client.inspect(max_depth=8, max_elements=200)
    el = next(e for e in els if e.get("automation_id") == "pnl_dragbox")
    b = frame_box(el, meta)
    return meta["observation_id"], client.target(bbox_frame_px=b), b


results["drag"] = timed(
    "drag(24 steps)", observe_from,
    lambda p: client.drag(from_target=p[1], to_target=p[1], observation_id=p[0],
                          max_age_ms=3000, steps=24),
    reps=8,
)

client.stop()
proc.terminate()

out = os.path.join("research", "phase7-latency.json")
os.makedirs("research", exist_ok=True)
with open(out, "w", encoding="utf-8") as fh:
    json.dump({k: round(v, 2) for k, v in results.items()}, fh, indent=2)
print(f"\nwrote {out}")
