"""JSON-lines resident controller. Run it once, then send bounded subgoals on stdin."""

from __future__ import annotations

import json
import sys
from time import monotonic

from controller import Controller, Subgoal
from images import ImageStore
from jev import JevChooser
from mac import MacDesktop


def write(message: dict) -> None:
    print(json.dumps(message), flush=True)


def main() -> int:
    desktop = MacDesktop()
    chooser = JevChooser()
    controller = Controller(desktop, chooser)
    with ImageStore() as images:
        write({"status": "ready"})
        for line in sys.stdin:
            try:
                command = json.loads(line)
                kind = command["command"]
                if kind == "close":
                    return 0
                if kind == "ack_image":
                    images.ack()
                    write({"status": "image_deleted"})
                    continue
                if kind != "run":
                    raise ValueError("unknown command")
                if images.has_pending:
                    raise ValueError("ack_image is required before another run")
                subgoal = Subgoal(**command["subgoal"])
                if not subgoal.instruction or not subgoal.expected_window:
                    raise ValueError("instruction and expected_window are required")
                if subgoal.max_actions < 1 or subgoal.max_seconds <= 0:
                    raise ValueError("max_actions and max_seconds must be positive")
                if subgoal.min_probability is not None and not 0 <= subgoal.min_probability <= 1:
                    raise ValueError("min_probability must be between 0 and 1")
                if subgoal.max_unchanged_retries < 0:
                    raise ValueError("max_unchanged_retries must be nonnegative")
                if subgoal.search_text and subgoal.max_unchanged_retries:
                    raise ValueError("text entry cannot use unchanged-screen retries")
                started = monotonic()
                result = controller.run(subgoal)
                output = {"status": result.status, "reason": result.reason, "actions": result.actions,
                          "run_wall_s": round(monotonic() - started, 3),
                          "timings": {name: round(value, 3) for name, value in controller.timings.items()}}
                if result.status == "needs_visual_inspection":
                    try:
                        output["window"], fresh_image = desktop.capture()
                    except Exception:
                        fresh_image = result.observation.image if result.observation else None
                    if fresh_image:
                        output["image_path"] = str(images.offer(fresh_image))
                write(output)
            except (KeyError, TypeError, ValueError) as exc:
                write({"status": "error", "reason": str(exc)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
