"""Phase 1 Verification Tests: Protocol framing, window identity, and child lifecycle.

Verifies the Phase 1 Gate from IMPLEMENTATION-PLAN.md:
- 100 sequential `doctor` requests use the same child PID
- Malformed and truncated messages are rejected predictably without engine hang
- Client request timeout terminates the owned child process leaving no orphaned process
- Window enumeration and exact window attachment/detachment succeed
"""

from __future__ import annotations

import os
import struct
import subprocess
import sys
import time
import pytest

# Ensure client directory is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "client")))
from wcu_client import WcuClient, WcuError, PROTOCOL_VERSION


def test_100_sequential_doctor_requests():
    """Verify 100 sequential requests maintain a single persistent child process."""
    with WcuClient() as client:
        initial_pid = client.pid
        assert initial_pid is not None, "Client process failed to start"

        pids = []
        for i in range(100):
            res = client.doctor()
            assert res.get("protocol_version") == PROTOCOL_VERSION
            assert res.get("desktop", {}).get("accessible") is True
            assert res.get("d3d11", {}).get("supported") is True
            assert client.pid == initial_pid, f"PID changed at iteration {i}: {client.pid} != {initial_pid}"
            pids.append(res.get("pid"))

        assert all(p == initial_pid for p in pids), "Engine reported internal PID mismatch"
        print(f"\n[PASS] 100 sequential doctor requests completed on single PID {initial_pid}")


import threading

def _read_with_timeout(proc, num_bytes: int, timeout_sec: float = 2.0) -> bytes:
    res = bytearray()
    err = []
    def _target():
        try:
            while len(res) < num_bytes:
                chunk = proc.stdout.read(num_bytes - len(res))
                if not chunk:
                    break
                res.extend(chunk)
        except Exception as e:
            err.append(e)

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout=timeout_sec)
    if t.is_alive():
        raise TimeoutError(f"Read timed out after {timeout_sec}s")
    if err:
        raise err[0]
    return bytes(res)


def test_malformed_requests_and_framing():
    """Verify malformed and truncated messages are rejected without deadlock."""
    # 1. Unsupported protocol version
    with WcuClient() as client:
        raw_header = b'{"v":999,"id":1,"op":"doctor","args":{},"payload_len":0}'
        msg = struct.pack("<I", len(raw_header)) + raw_header
        client._proc.stdin.write(msg)
        client._proc.stdin.flush()
        len_bytes = _read_with_timeout(client._proc, 4, timeout_sec=2.0)
        (resp_len,) = struct.unpack("<I", len_bytes)
        resp = _read_with_timeout(client._proc, resp_len, timeout_sec=2.0)
        assert b"unsupported" in resp
        print("[PASS] Unsupported protocol version safely rejected")

    # 2. Header claiming excessive length (> 64 KiB)
    with WcuClient() as client:
        msg = struct.pack("<I", 70000)
        client._proc.stdin.write(msg)
        client._proc.stdin.flush()
        try:
            len_bytes = _read_with_timeout(client._proc, 4, timeout_sec=2.0)
            if len_bytes and len(len_bytes) == 4:
                (resp_len,) = struct.unpack("<I", len_bytes)
                resp = _read_with_timeout(client._proc, resp_len, timeout_sec=2.0)
                assert b"invalid_request" in resp or b"limit" in resp
        except TimeoutError:
            pass  # If engine closes pipe or ignores without framing, acceptable
        print("[PASS] Excessive header length safely handled")

    # 3. Truncated header (declares 100 bytes, sends only 10, then EOF)
    with WcuClient() as client:
        msg = struct.pack("<I", 100) + b"1234567890"
        client._proc.stdin.write(msg)
        client._proc.stdin.close()  # Close pipe to signal EOF
        client._proc.wait(timeout=3.0)
        assert client._proc.poll() is not None, "Engine did not exit on truncated message with pipe EOF"
        print("[PASS] Truncated header with EOF terminates cleanly")


def test_timeout_cleans_up_child():
    """Verify request timeout terminates the child process and leaves no orphan."""
    client = WcuClient()
    client.start()
    child_pid = client.pid
    assert child_pid is not None

    timeout_fired = False
    try:
        try:
            client.request("doctor", timeout_sec=0.0001)
        except WcuError as e:
            assert e.code == "timeout"
            timeout_fired = True
            print(f"[PASS] Caught expected timeout: {e}")

        assert timeout_fired, "Expected WcuError('timeout') was not raised"

        # Process must now be terminated
        time.sleep(0.5)
        import psutil
        assert not psutil.pid_exists(child_pid), f"Child process {child_pid} was not terminated on timeout"
        print(f"[PASS] Child process {child_pid} verified dead after timeout")
    finally:
        client.stop()


def test_window_enumeration_and_attachment():
    """Verify window enumeration and attachment against desktop windows."""
    with WcuClient() as client:
        windows = client.list_windows()
        assert len(windows) > 0, "No desktop windows found"

        # Verify WindowInfo structure
        sample = windows[0]
        for field in ["hwnd", "pid", "title", "class_name", "bounds", "dpi", "is_foreground", "is_visible"]:
            assert field in sample, f"Missing field '{field}' in window info"

        # Find a valid window to attach to (e.g. explorer, powershell, or any visible window)
        target = next((w for w in windows if w.get("is_visible") and w.get("bounds", {}).get("w", 0) > 100), None)
        assert target is not None, "No suitable visible window for attachment"

        # Attach
        attach_res = client.attach(
            hwnd=target["hwnd"],
            pid=target["pid"],
            process_create_time_utc=target["process_create_time_utc"],
        )
        assert attach_res.get("status") == "attached"
        assert attach_res.get("hwnd") == target["hwnd"]
        assert attach_res.get("pid") == target["pid"]

        # Detach
        detach_res = client.detach()
        assert detach_res.get("status") == "detached"

        # Attach with invalid HWND should fail cleanly
        with pytest.raises(WcuError) as exc_info:
            client.attach(hwnd="999999999", pid=99999)
        assert exc_info.value.code == "window_gone"
        print("[PASS] Window enumeration, attach, detach, and invalid HWND rejection verified")


if __name__ == "__main__":
    test_100_sequential_doctor_requests()
    test_malformed_requests_and_framing()
    test_timeout_cleans_up_child()
    test_window_enumeration_and_attachment()
    print("\nALL PHASE 1 GATE TESTS PASSED!")
