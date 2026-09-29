"""Run the real model/image/action loop on the controlled desktop fixture."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client"))

from model_control import run_session
from vision_model import VisionModel
from wcu_client import WcuClient


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=os.getenv("WCU_MODEL", "gpt-6-luna"))
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--move-window-before-action", action="store_true", help="Exercise the stale-target rejection path")
    args = parser.parse_args()
    token = os.getenv("WCU_PROXY_TOKEN")
    if not token:
        parser.error("Set WCU_PROXY_TOKEN for the local model proxy")
    fixture = ROOT / "tests" / "fixture_app" / "bin" / "Debug" / "net6.0-windows" / "fixture_app.exe"
    if not fixture.is_file():
        parser.error("Build the fixture application first")

    user32 = ctypes.windll.user32
    desktop = user32.OpenInputDesktop(0, False, 0x01FF)
    if not desktop:
        parser.error("No accessible interactive desktop")
    try:
        if not user32.SetThreadDesktop(desktop):
            parser.error("Could not attach to the interactive desktop")
    finally:
        user32.CloseDesktop(desktop)

    process = subprocess.Popen([str(fixture)])
    try:
        model = VisionModel("http://127.0.0.1:8317/v1/chat/completions", args.model, token)
        with WcuClient() as client:
            window = None
            for _ in range(30):
                matches = [w for w in client.list_windows() if w.get("pid") == process.pid]
                if len(matches) == 1:
                    window = matches[0]
                    break
                time.sleep(0.2)
            if window is None:
                raise RuntimeError("Fixture window was not found")
            client.attach(window["hwnd"], window["pid"], window["process_create_time_utc"])
            user32.SetForegroundWindow(int(window["hwnd"]))
            time.sleep(0.25)
            if args.move_window_before_action:
                base_model = model

                class MovingModel:
                    def decide(self, task, png, width, height):
                        decision = base_model.decide(task, png, width, height)
                        if not user32.MoveWindow(int(window["hwnd"]), 260, 240, 500, 390, True):
                            raise RuntimeError("Could not move fixture for stale-target check")
                        return decision

                    def verify(self, task, before_png, after_png, width, height):
                        return base_model.verify(task, before_png, after_png, width, height)

                model = MovingModel()
            outcome = run_session(
                client,
                model,
                "Click Continue exactly once and verify the visible Counter changes from 0 to 1",
                execute=args.execute,
                max_decisions=1 if args.move_window_before_action else 2,
            )
            print(json.dumps(outcome, sort_keys=True))
            if args.execute:
                elements, _ = client.inspect(max_depth=6, max_elements=60)
                counter = next((e.get("name") for e in elements if e.get("automation_id") == "lbl_counter"), None)
                print(json.dumps({"fixture_counter": counter}, sort_keys=True))
                expected_status = "stale_target" if args.move_window_before_action else "verified"
                if outcome.get("status") != expected_status:
                    raise RuntimeError(f"Session outcome check failed: expected {expected_status}, got {outcome.get('status')}")
                expected = "Counter: 0" if args.move_window_before_action else "Counter: 1"
                if counter != expected:
                    raise RuntimeError(f"Fixture action check failed: expected {expected}, got {counter}")
    finally:
        process.terminate()
        process.wait(timeout=5)


if __name__ == "__main__":
    main()
