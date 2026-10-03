"""Canonical Cua Driver smoke check: compute 6 x 7 in Calculator and verify 42."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from typing import Final, TypedDict

import pytest

CUA_DRIVER_CMD: Final[str] = "cua-driver"


class ElementInfo(TypedDict, total=False):
    """Structured element descriptor from get_window_state."""

    element_token: str
    element_index: int
    label: str
    role: str
    depth: int


class WindowInfo(TypedDict, total=False):
    """Window geometry and process metadata."""

    pid: int
    window_id: int
    title: str


class LaunchAppResponse(TypedDict, total=False):
    """Response returned by launch_app tool."""

    pid: int
    name: str
    active: bool
    running: bool
    windows: list[WindowInfo]


class WindowStateResponse(TypedDict, total=False):
    """Response returned by get_window_state tool."""

    elements: list[ElementInfo]
    snapshot_id: str
    element_count: int


class VerifyPredicateResult(TypedDict, total=False):
    """Individual predicate status in verify_state response."""

    index: int
    status: str


class VerifyStateResponse(TypedDict, total=False):
    """Response returned by verify_state tool."""

    status: str
    stable: bool
    samples: int
    predicates: list[VerifyPredicateResult]


class ListWindowsResponse(TypedDict, total=False):
    """Response returned by list_windows tool."""

    windows: list[WindowInfo]
    _legacy_windows: list[WindowInfo]


def is_driver_available() -> bool:
    """Return True if running on Windows with cua-driver available on PATH."""
    return sys.platform == "win32" and shutil.which(CUA_DRIVER_CMD) is not None


def call_driver_raw(tool_name: str, payload_json: str, *, timeout_s: float = 15.0) -> str:
    """Execute a single tool via cua-driver call using raw JSON string."""
    cmd = [CUA_DRIVER_CMD, "call", tool_name, payload_json]
    process = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )
    if process.returncode != 0:
        error_msg = process.stderr.strip() or process.stdout.strip()
        raise RuntimeError(f"cua-driver call {tool_name} failed (code {process.returncode}): {error_msg}")
    return process.stdout


def launch_calculator() -> LaunchAppResponse:
    """Launch the Windows Calculator app via launch_app tool."""
    raw = call_driver_raw("launch_app", json.dumps({"name": "Calculator"}))
    data: LaunchAppResponse = json.loads(raw)
    return data


def list_open_windows() -> ListWindowsResponse:
    """List all open desktop windows via list_windows tool."""
    raw = call_driver_raw("list_windows", "{}")
    data: ListWindowsResponse = json.loads(raw)
    return data


def get_calculator_state(pid: int, window_id: int) -> WindowStateResponse:
    """Capture current Calculator accessibility tree via get_window_state."""
    payload = json.dumps({"pid": pid, "window_id": window_id})
    raw = call_driver_raw("get_window_state", payload)
    data: WindowStateResponse = json.loads(raw)
    return data


def click_element(pid: int, window_id: int, token: str) -> None:
    """Click an element by its token using background delivery."""
    payload = json.dumps(
        {
            "pid": pid,
            "window_id": window_id,
            "element_token": token,
            "delivery_mode": "background",
        }
    )
    call_driver_raw("click", payload)


def verify_calculator_display(pid: int, window_id: int, expected_substring: str) -> VerifyStateResponse:
    """Verify that the Calculator window displays the expected text."""
    payload = json.dumps(
        {
            "pid": pid,
            "window_id": window_id,
            "expect": [
                {
                    "element": {
                        "selector": {
                            "label_contains": expected_substring,
                        }
                    }
                }
            ],
            "stable_samples": 2,
            "timeout_ms": 5000,
        }
    )
    raw = call_driver_raw("verify_state", payload)
    data: VerifyStateResponse = json.loads(raw)
    return data


def find_token_by_label(elements: list[ElementInfo], expected_label: str) -> str:
    """Find the element_token for the first element matching expected_label."""
    for elem in elements:
        label = elem.get("label", "")
        if expected_label.lower() in label.lower():
            token = elem.get("element_token")
            if token:
                return token
    raise ValueError(f"Could not find element with label containing '{expected_label}'")


@pytest.mark.skipif(not is_driver_available(), reason="cua-driver not available on this platform")
def test_calculator_6x7_equals_42_smoke() -> None:
    """Canonical smoke seam: 6 x 7 in Windows Calculator verifies to 42."""
    launch_resp = launch_calculator()
    pid = launch_resp.get("pid")
    windows = launch_resp.get("windows", [])

    # If windows list is pending, locate Calculator in list_windows
    window_id: int | None = None
    if windows:
        window_id = windows[0].get("window_id")
    else:
        time.sleep(1.0)
        list_resp = list_open_windows()
        candidates = list_resp.get("windows", []) or list_resp.get("_legacy_windows", [])
        for win in candidates:
            if "calculator" in win.get("title", "").lower():
                pid = win.get("pid")
                window_id = win.get("window_id")
                break

    assert pid is not None, "Failed to resolve Calculator PID"
    assert window_id is not None, "Failed to resolve Calculator window_id"

    try:
        # Step 1: Initial observation
        state = get_calculator_state(pid, window_id)
        elements = state.get("elements", [])
        assert elements, "get_window_state returned empty elements list"

        # Step 2: Click 'Six'
        six_token = find_token_by_label(elements, "Six")
        click_element(pid, window_id, six_token)

        # Step 3: Refresh state and click 'Multiply'
        state = get_calculator_state(pid, window_id)
        elements = state.get("elements", [])
        mult_token = find_token_by_label(elements, "Multiply")
        click_element(pid, window_id, mult_token)

        # Step 4: Refresh state and click 'Seven'
        state = get_calculator_state(pid, window_id)
        elements = state.get("elements", [])
        seven_token = find_token_by_label(elements, "Seven")
        click_element(pid, window_id, seven_token)

        # Step 5: Refresh state and click 'Equals'
        state = get_calculator_state(pid, window_id)
        elements = state.get("elements", [])
        equals_token = find_token_by_label(elements, "Equals")
        click_element(pid, window_id, equals_token)

        # Step 6: Verify postcondition: displays 42
        verify_resp = verify_calculator_display(pid, window_id, "42")

        assert verify_resp.get("status") == "satisfied", f"Verification failed: {verify_resp}"
        assert verify_resp.get("stable") is True, f"Verification was not stable: {verify_resp}"

    finally:
        # Graceful cleanup of Calculator app
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
