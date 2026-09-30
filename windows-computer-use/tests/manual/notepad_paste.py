"""One opt-in live Notepad acceptance test. Not collected by the test suite.

Run this file directly with --evidence-dir. It closes the window it opened and
stops only the session it created, so nothing is left behind on the desktop.
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


def clipboard_contents():
    win32clipboard.OpenClipboard()
    try:
        formats = []
        format_id = 0
        while True:
            format_id = win32clipboard.EnumClipboardFormats(format_id)
            if not format_id:
                break
            assert format_id in (1, 7, 13, 16), (
                "This test needs an empty or plain-text clipboard, because the paste "
                f"method refuses anything else. Found clipboard format {format_id}: "
                "rich contents such as an image, HTML, or files cannot be snapshotted "
                "and put back, so paste will not run while they are on the clipboard."
            )
            formats.append(format_id)
        return {format_id: win32clipboard.GetClipboardData(format_id) for format_id in formats}
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

    The paste modifies the document, so a bare close raises Notepad's save
    prompt and the window stays open. Undoing that single paste returns the
    document to its original empty state, which clears the modified flag and
    makes the close prompt-free. The flag is checked rather than assumed, so a
    document that is still modified falls through to the last resort instead of
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
    before_clipboard = clipboard_contents()

    with tempfile.TemporaryDirectory(prefix="wcu-notepad-paste-") as session_home:
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
                (evidence_dir / "notepad-paste-result.json").write_text(
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
            assert initial["value"] == "", "Refusing to edit a nonempty Notepad document"
            bounds = observation["capture_bounds_physical_px"]
            area = initial["bounds"]
            x = area["x"] - bounds["x"] + min(100, area["w"] // 2)
            y = area["y"] - bounds["y"] + min(100, area["h"] // 2)
            cli("act", "click", "--observation-id", str(observation["observation_id"]),
                "--point", str(x), str(y), "--max-age-ms", "15000")
            # This is the only text dispatch in the test. No fallback or retry.
            result = cli("act", "type", "--method", "paste", "--text", SENTENCE,
                         "--max-age-ms", "30000")
            dispatch = result["dispatch"]
            actual = editor(cli("inspect", "--max-elements", "80")["elements"])["value"]
            evidence_dir.mkdir(parents=True, exist_ok=True)
            image = evidence_dir / "notepad-paste.png"
            shutil.copyfile(result["evidence"]["image_path"], image)
            report = {
                "expected": SENTENCE, "actual": actual,
                "verification": dispatch["verification"],
                "events_injected": dispatch["events_injected"],
                "clipboard": dispatch["clipboard"],
                "clipboard_contents_restored": clipboard_contents() == before_clipboard,
                "screenshot": str(image),
            }
            (evidence_dir / "notepad-paste-result.json").write_text(
                json.dumps(report, indent=2) + "\n", encoding="utf-8")
            assert actual == SENTENCE, report
            assert dispatch["verification"] == "matched", report
            assert dispatch["events_injected"] == 4, report
            assert dispatch["clipboard"] == "restored", report
            assert report["clipboard_contents_restored"], report
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
    print("PASS: one Notepad paste test")
    print(json.dumps(report, indent=2))
