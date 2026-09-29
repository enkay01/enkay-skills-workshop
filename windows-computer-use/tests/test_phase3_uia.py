"""Phase 3 Verification Tests: Native UI Automation (UIA) tree inspection and semantic actions.

Verifies the Phase 3 Gate from IMPLEMENTATION-PLAN.md:
- Locate the fixture button and editable field
- Invoke button once via InvokePattern and verify counter increments
- Set field value via ValuePattern and verify text update through a fresh inspection
- Verify empty / missing patterns return typed errors predictably
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "client")))
from wcu_client import WcuClient, WcuError


def launch_native_fixture():
    """Launch the compiled .NET WinForms fixture application."""
    exe_path = os.path.abspath(
        os.path.join(
            os.path.dirname(__file__),
            "fixture_app",
            "bin",
            "Debug",
            "net6.0-windows",
            "fixture_app.exe",
        )
    )
    if not os.path.exists(exe_path):
        raise FileNotFoundError(f"Fixture executable not found at '{exe_path}'. Run dotnet build.")

    proc = subprocess.Popen([exe_path])
    time.sleep(1.5)
    return proc


def test_uia_inspection_and_semantic_actions():
    """Verify Phase 3 Gate: inspect, InvokePattern, ValuePattern, and verification."""
    fixture_proc = launch_native_fixture()
    try:
        with WcuClient() as client:
            wins = client.list_windows()
            target = next((w for w in wins if "WCU_Native_WinForms_Fixture" in w.get("title", "")), None)
            assert target is not None, "WinForms fixture window not found in list_windows"

            # Attach
            client.attach(target["hwnd"], target["pid"], target["process_create_time_utc"])
            print(f"\nAttached to fixture PID {target['pid']}, HWND {target['hwnd']}")

            # Step 1: Initial Inspection
            elements, truncated = client.inspect(max_depth=6, max_elements=100)
            assert not truncated, "Inspection was unexpectedly truncated"
            assert len(elements) > 0, "No UIA elements returned"

            print(f"Discovered {len(elements)} UIA elements:")
            for e in elements:
                print(f"  Token: {e['token']} | Type: {e['control_type']} | Name: '{e['name']}' | Id: '{e['automation_id']}' | Patterns: {e['supported_patterns']}")

            # Find controls
            btn = next((e for e in elements if e["automation_id"] == "btn_continue" or e["name"] == "Continue"), None)
            txt = next((e for e in elements if e["automation_id"] == "txt_input" or e["name"] == "Input Field"), None)
            counter = next((e for e in elements if e["automation_id"] == "lbl_counter" or "Counter" in e["name"]), None)

            assert btn is not None, "Button 'btn_continue' not found in UIA tree"
            assert txt is not None, "TextBox 'txt_input' not found in UIA tree"
            assert counter is not None, "Label 'lbl_counter' not found in UIA tree"

            assert "Invoke" in btn["supported_patterns"], f"Button missing Invoke pattern: {btn['supported_patterns']}"
            assert "Value" in txt["supported_patterns"], f"TextBox missing Value pattern: {txt['supported_patterns']}"
            assert "0" in counter["name"], f"Initial counter should be 0, got '{counter['name']}'"

            # Step 2: Semantic Invoke action on button
            print("\nInvoking Continue button via UIA InvokePattern...")
            res_invoke = client.uia_action(btn["token"], action="invoke")
            assert res_invoke.get("status") == "invoked"

            # Step 3: Fresh inspection to verify counter changed
            time.sleep(0.2)
            elements2, _ = client.inspect(max_depth=6, max_elements=100)
            counter2 = next((e for e in elements2 if e["automation_id"] == "lbl_counter" or "Counter" in e["name"]), None)
            assert counter2 is not None
            print(f"Counter after invoke: '{counter2['name']}'")
            assert "1" in counter2["name"], f"Counter was not incremented: '{counter2['name']}'"

            # Step 4: Semantic SetValue action on editable field
            new_text = "Verified UIA Text Input 2026"
            print(f"\nSetting TextBox value to '{new_text}' via UIA ValuePattern...")
            # Re-locate txt element token from fresh inspection
            txt2 = next((e for e in elements2 if e["automation_id"] == "txt_input" or e["name"] == "Input Field"), None)
            assert txt2 is not None
            res_value = client.uia_action(txt2["token"], action="set_value", value=new_text)
            assert res_value.get("status") == "value_set"

            # Step 5: Fresh inspection to verify value
            elements3, _ = client.inspect(max_depth=6, max_elements=100)
            txt3 = next((e for e in elements3 if e["automation_id"] == "txt_input" or e["name"] == new_text or e["name"] == "Input Field"), None)
            print(f"TextBox after set_value: name='{txt3['name']}', value='{txt3.get('value')}'")
            assert new_text in txt3.get("value", "") or new_text in txt3["name"], f"TextBox text not updated to '{new_text}'"

            # Step 6: Negative tests: stale tokens and unsupported patterns
            print("\nTesting negative cases (stale token, unsupported pattern)...")
            with pytest.raises(WcuError) as exc_info:
                client.uia_action("invalid_token_999", action="invoke")
            assert exc_info.value.code == "stale_element"
            print(f"  [PASS] Stale token rejected: {exc_info.value}")

            # 6a. Stale token from previous inspection is rejected
            with pytest.raises(WcuError) as exc_info:
                client.uia_action(counter2["token"], action="invoke")
            assert exc_info.value.code == "stale_element"
            print(f"  [PASS] Stale token from earlier observation rejected: {exc_info.value}")

            # 6b. Current token for element that does not support pattern is rejected as unsupported
            counter3 = next((e for e in elements3 if e["automation_id"] == "lbl_counter"), None)
            assert counter3 is not None
            with pytest.raises(WcuError) as exc_info:
                client.uia_action(counter3["token"], action="invoke")
            assert exc_info.value.code == "unsupported"
            print(f"  [PASS] Unsupported pattern rejected: {exc_info.value}")

            print("\nALL PHASE 3 UIA ACTIONS VERIFIED!")
    finally:
        fixture_proc.terminate()
        fixture_proc.wait(timeout=2.0)


if __name__ == "__main__":
    test_uia_inspection_and_semantic_actions()
    print("\nALL PHASE 3 GATE TESTS PASSED!")
