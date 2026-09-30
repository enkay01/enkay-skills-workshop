"""One opt-in live Notepad acceptance test for the commit method. Not collected.

Run this file directly with --evidence-dir. It closes the window it opened and
stops only the session it created, so nothing is left behind on the desktop.

Unlike the paste test this dispatch never touches the clipboard: the
assertion is that the clipboard is byte-identical afterwards, whatever it
held beforehand.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import win32clipboard

SENTENCE = "wcu typed this sentence."


def clipboard_snapshot():
    win32clipboard.OpenClipboard()
    try:
        contents = {}
        format_id = 0
        while True:
            format_id = win32clipboard.EnumClipboardFormats(format_id)
            if not format_id:
                break
            try:
                contents[format_id] = win32clipboard.GetClipboardData(format_id)
            except Exception:
                contents[format_id] = None
        return contents
    finally:
        win32clipboard.CloseClipboard()


def editor(elements):
    matches = [e for e in elements if e["control_type"] == "Control_50030"]
    assert len(matches) == 1, "Expected one Notepad document in the attached window"
    assert "Value" in matches[0]["supported_patterns"], "Notepad editor must expose its actual value"
    return matches[0]


def window_record(cli, hwnd):
    matches = [w for w in cli("windows")["windows"] if str(w["hwnd"]) == str(hwnd)]
    return matches[0] if matches else None


def terminate_if_sole_document(cli, hwnd):
    """Last resort: stop the process, but only when it owns no other document.

    Notepad hosts every open document in one process, so stopping it blindly
    would close windows this test never opened.
    """
    record = window_record(cli, hwnd)
    if record is None:
        return
    pid = record["pid"]
    documents = [
        w for w in cli("windows")["windows"]
        if w["pid"] == pid and w["class_name"] == "Notepad"
    ]
    assert len(documents) == 1, (
        f"Refusing to stop pid {pid}: it owns {len(documents)} Notepad windows, "
        "so terminating it would close windows this test did not open"
    )
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    time.sleep(1.5)


def close_notepad(cli, hwnd):
    """Close the Notepad window this test opened, leaving no prompt behind.

    The commit is a single undoable edit, so undoing it returns the document
    to its original empty state, which clears the modified flag and makes the
    close prompt-free. The flag is checked rather than assumed, so a document
    that is still modified falls through to the last resort instead of
    silently leaving a prompt on screen.
    """
    if window_record(cli, hwnd) is None:
        return

    cli("switch", hwnd)
    cli("act", "press", "--chord", "ctrl+z", "--max-age-ms", "30000")
    time.sleep(0.6)
    title = window_record(cli, hwnd)["title"]
    if not title.startswith("*"):
        cli("act", "press", "--chord", "alt+f4", "--max-age-ms", "30000")
        time.sleep(1.5)
        if window_record(cli, hwnd) is None:
            return

    terminate_if_sole_document(cli, hwnd)
    assert window_record(cli, hwnd) is None, f"Notepad window {hwnd} was left open by the test"


def run(evidence_dir):
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "cli") + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    before_clipboard = clipboard_snapshot()

    with tempfile.TemporaryDirectory(prefix="wcu-notepad-commit-") as session_home:
        env["WCU_HOME"] = session_home

        def cli(*args):
            result = subprocess.run(
                [sys.executable, "-m", "wcu", *args], env=env,
                capture_output=True, text=True, encoding="utf-8", timeout=40,
            )
            assert result.returncode == 0, result.stderr
            envelope = json.loads(result.stdout)
            if not envelope["ok"]:
                evidence_dir.mkdir(parents=True, exist_ok=True)
                failure = {"status": "failed", "command": args[0], "error": envelope.get("error")}
                (evidence_dir / "notepad-commit-result.json").write_text(
                    json.dumps(failure, indent=2) + "\n", encoding="utf-8")
            assert envelope["ok"], envelope.get("error")
            return envelope["result"]

        cli("session", "start", "--engine", str(root / "engine/target/release/wcu-engine.exe"))
        hwnd = None
        try:
            before = {w["hwnd"] for w in cli("windows")["windows"]}
            cli("open", "notepad")
            time.sleep(2.5)
            added = [w for w in cli("windows")["windows"]
                     if w["hwnd"] not in before and w["class_name"] == "Notepad"]
            assert len(added) == 1, "Expected one newly opened Notepad window"
            hwnd = added[0]["hwnd"]
            observation = cli("switch", hwnd)["observation"]
            initial = editor(cli("inspect", "--max-elements", "80")["elements"])
            if initial["value"] != "":
                # Setup, not the dispatch under test: a restored session means
                # the new window is not empty. Clear it so the commit starts
                # from a deterministic empty document.
                cli("act", "set-value", "--token", initial["token"], "--value", "")
                time.sleep(0.4)
                initial = editor(cli("inspect", "--max-elements", "80")["elements"])
                assert initial["value"] == "", "Could not reach an empty Notepad document"
            bounds = observation["capture_bounds_physical_px"]
            area = initial["bounds"]
            x = area["x"] - bounds["x"] + min(100, area["w"] // 2)
            y = area["y"] - bounds["y"] + min(100, area["h"] // 2)
            cli("act", "click", "--observation-id", str(observation["observation_id"]),
                "--point", str(x), str(y), "--max-age-ms", "15000")
            # This is the only text dispatch in the test. No fallback or retry.
            result = cli("act", "type", "--method", "commit", "--text", SENTENCE,
                         "--max-age-ms", "30000")
            dispatch = result["dispatch"]
            actual = editor(cli("inspect", "--max-elements", "80")["elements"])["value"]
            evidence_dir.mkdir(parents=True, exist_ok=True)
            image = evidence_dir / "notepad-commit.png"
            shutil.copyfile(result["evidence"]["image_path"], image)
            report = {
                "expected": SENTENCE, "actual": actual,
                "verification": dispatch["verification"],
                "verify_via": dispatch.get("verify_via"),
                "messages_sent": dispatch.get("messages_sent"),
                "events_injected": dispatch.get("events_injected"),
                "clipboard": dispatch["clipboard"],
                "clipboard_untouched": clipboard_snapshot() == before_clipboard,
                "screenshot": str(image),
            }
            (evidence_dir / "notepad-commit-result.json").write_text(
                json.dumps(report, indent=2) + "\n", encoding="utf-8")
            assert actual == SENTENCE, report
            assert dispatch["verification"] == "matched", report
            assert dispatch["messages_sent"] == 1, report
            assert dispatch["events_injected"] == 0, report
            assert dispatch["clipboard"] == "unchanged", report
            assert report["clipboard_untouched"], report
            # The test is only finished once the window it opened is gone.
            close_notepad(cli, hwnd)
            hwnd = None
            return report
        finally:
            if hwnd is not None:
                # Failure path: still leave no window behind, but never mask the
                # assertion that actually failed.
                try:
                    close_notepad(cli, hwnd)
                except Exception as cleanup_error:
                    print(f"WARNING: could not close Notepad window {hwnd}: {cleanup_error}")
            cli("session", "stop")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    # Buffer every child command; console output during the chain can steal focus.
    report = run(args.evidence_dir)
    print("PASS: one Notepad commit test")
    print(json.dumps(report, indent=2))
