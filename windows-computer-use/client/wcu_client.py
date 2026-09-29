"""Windows Computer Use Client (Python IPC to Rust Engine).

Implements binary framed IPC over stdin/stdout with deadline handling,
thread-safe response reading, and strict message framing.
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Tuple


PROTOCOL_VERSION = 1
MAX_HEADER_LEN = 65536
MAX_PAYLOAD_LEN = 134217728


class WcuError(Exception):
    def __init__(self, code: str, message: str, details: Any = None):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.details = details


class WcuClient:
    def __init__(self, engine_path: Optional[str] = None):
        if engine_path is None:
            # Default to debug build in sibling engine directory
            cur_dir = os.path.dirname(os.path.abspath(__file__))
            engine_path = os.path.abspath(os.path.join(cur_dir, "..", "engine", "target", "debug", "wcu-engine.exe"))
        
        self.engine_path = engine_path
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.RLock()
        self._req_id = 0
        self._stderr_thread: Optional[threading.Thread] = None
        self._stderr_lines: List[str] = []
        self._is_alive = False

    def start(self) -> None:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return

            if not os.path.exists(self.engine_path):
                raise FileNotFoundError(f"Engine binary not found at '{self.engine_path}'. Did you run cargo build?")

            self._proc = subprocess.Popen(
                [self.engine_path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
            self._is_alive = True
            self._stderr_lines = []

            def _drain_stderr():
                while self._proc and self._proc.stderr:
                    line = self._proc.stderr.readline()
                    if not line:
                        break
                    decoded = line.decode("utf-8", errors="replace").strip()
                    if decoded:
                        self._stderr_lines.append(decoded)

            self._stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
            self._stderr_thread.start()

    def _stop_locked(self) -> None:
        if self._proc is None:
            return
        proc = self._proc
        try:
            if proc.poll() is None:
                # Attempt clean shutdown request if pipes are open
                try:
                    self._raw_request("shutdown", {}, timeout_sec=1.0)
                except Exception:
                    pass
                proc.terminate()
                proc.wait(timeout=2.0)
        except Exception:
            if proc.poll() is None:
                proc.kill()
        finally:
            self._proc = None
            self._is_alive = False

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    @property
    def pid(self) -> Optional[int]:
        return self._proc.pid if self._proc else None

    def _read_exact(self, num_bytes: int) -> bytes:
        proc = self._proc
        if proc is None or proc.stdout is None:
            raise WcuError("engine_dead", "Engine process or stdout is None")
        data = bytearray()
        while len(data) < num_bytes:
            chunk = proc.stdout.read(num_bytes - len(data))
            if not chunk:
                raise EOFError(f"Unexpected EOF while reading {num_bytes} bytes (got {len(data)})")
            data.extend(chunk)
        return bytes(data)

    def _raw_request(
        self,
        op: str,
        args: Dict[str, Any],
        payload: bytes = b"",
        timeout_sec: float = 10.0,
    ) -> Tuple[Dict[str, Any], bytes]:
        proc = self._proc
        if not self._is_alive or proc is None or proc.poll() is not None:
            raise WcuError("engine_dead", "Engine process is not running")

        self._req_id += 1
        req_id = self._req_id

        header_obj = {
            "v": PROTOCOL_VERSION,
            "id": req_id,
            "op": op,
            "args": args,
            "payload_len": len(payload),
        }
        header_bytes = json.dumps(header_obj).encode("utf-8")
        if len(header_bytes) > MAX_HEADER_LEN:
            raise ValueError(f"Request header exceeds {MAX_HEADER_LEN} bytes")

        # Framing: u32 little endian header length + header bytes + payload
        msg = struct.pack("<I", len(header_bytes)) + header_bytes + payload

        result_holder: List[Any] = []
        error_holder: List[Exception] = []

        def _read_exact_proc(num_bytes: int) -> bytes:
            data = bytearray()
            while len(data) < num_bytes:
                chunk = proc.stdout.read(num_bytes - len(data))
                if not chunk:
                    raise EOFError(f"Unexpected EOF while reading {num_bytes} bytes (got {len(data)})")
                data.extend(chunk)
            return bytes(data)

        def _do_io():
            try:
                proc.stdin.write(msg)
                proc.stdin.flush()

                # Read response length
                len_bytes = _read_exact_proc(4)
                (resp_len,) = struct.unpack("<I", len_bytes)
                if resp_len > MAX_HEADER_LEN:
                    raise WcuError("invalid_response", f"Response header length {resp_len} exceeds limit")

                resp_bytes = _read_exact_proc(resp_len)
                resp_obj = json.loads(resp_bytes.decode("utf-8"))

                resp_payload = b""
                resp_payload_len = resp_obj.get("payload_len", 0)
                if resp_payload_len > MAX_PAYLOAD_LEN:
                    raise WcuError("invalid_response", f"Payload length {resp_payload_len} exceeds limit")
                if resp_payload_len > 0:
                    resp_payload = _read_exact_proc(resp_payload_len)

                result_holder.append((resp_obj, resp_payload))
            except Exception as e:
                error_holder.append(e)

        io_thread = threading.Thread(target=_do_io, daemon=True)
        io_thread.start()
        io_thread.join(timeout=timeout_sec)

        if io_thread.is_alive():
            # Timeout hit! Terminate child process immediately to prevent stale state
            self._stop_locked()
            raise WcuError("timeout", f"Operation '{op}' timed out after {timeout_sec:.2f}s")

        if error_holder:
            self._stop_locked()
            err = error_holder[0]
            if isinstance(err, WcuError):
                raise err
            raise WcuError("io_error", f"Engine communication failed: {err}")

        resp_obj, resp_payload = result_holder[0]
        if not resp_obj.get("ok"):
            err_dict = resp_obj.get("error", {})
            raise WcuError(
                code=err_dict.get("code", "unknown_error"),
                message=err_dict.get("message", "Operation failed"),
                details=err_dict.get("details"),
            )

        return resp_obj.get("result", {}), resp_payload

    def request(self, op: str, args: Optional[Dict[str, Any]] = None, timeout_sec: float = 10.0) -> Dict[str, Any]:
        with self._lock:
            res, _ = self._raw_request(op, args or {}, timeout_sec=timeout_sec)
            return res

    def doctor(self) -> Dict[str, Any]:
        return self.request("doctor")

    def list_windows(self) -> List[Dict[str, Any]]:
        res = self.request("list_windows")
        return res.get("windows", [])

    def list_monitors(self) -> List[Dict[str, Any]]:
        res = self.request("list_monitors")
        return res.get("monitors", [])

    def attach(self, hwnd: str, pid: int, process_create_time_utc: str = "unknown") -> Dict[str, Any]:
        return self.request("attach", {
            "hwnd": str(hwnd),
            "pid": pid,
            "process_create_time_utc": process_create_time_utc,
        })

    def attach_monitor(
        self,
        monitor_index: Optional[int] = None,
        hmonitor: Optional[str] = None,
        device_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        args: Dict[str, Any] = {}
        if monitor_index is not None:
            args["monitor_index"] = monitor_index
        if hmonitor is not None:
            args["hmonitor"] = str(hmonitor)
        if device_name is not None:
            args["device_name"] = str(device_name)
        return self.request("attach_monitor", args)

    def observe_monitor(
        self,
        monitor_index: Optional[int] = None,
        hmonitor: Optional[str] = None,
        device_name: Optional[str] = None,
        after_frame_id: int = 0,
        timeout_ms: int = 2000,
    ) -> Tuple[Dict[str, Any], bytes]:
        with self._lock:
            args: Dict[str, Any] = {
                "after_frame_id": after_frame_id,
                "timeout_ms": timeout_ms,
            }
            if monitor_index is not None:
                args["monitor_index"] = monitor_index
            if hmonitor is not None:
                args["hmonitor"] = str(hmonitor)
            if device_name is not None:
                args["device_name"] = str(device_name)
            meta, payload = self._raw_request(
                "observe_monitor",
                args,
                timeout_sec=(timeout_ms / 1000.0) + 3.0,
            )
            return meta, payload

    def focus_window(self, hwnd: str | int, attach: bool = False) -> Dict[str, Any]:
        result = self.request("focus_window", {"hwnd": str(hwnd)})
        if attach:
            windows = self.list_windows()
            matching = [w for w in windows if str(w.get("hwnd")) == str(hwnd)]
            if matching:
                target = matching[0]
                self.attach(
                    target["hwnd"],
                    target["pid"],
                    target["process_create_time_utc"],
                )
        return result

    def detach(self) -> Dict[str, Any]:
        return self.request("detach")

    def observe(
        self,
        after_frame_id: int = 0,
        timeout_ms: int = 2000,
        monitor_index: Optional[int] = None,
        hmonitor: Optional[Union[str, int]] = None,
        device_name: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], bytes]:
        with self._lock:
            args: Dict[str, Any] = {
                "after_frame_id": after_frame_id,
                "timeout_ms": timeout_ms,
            }
            if monitor_index is not None:
                args["monitor_index"] = monitor_index
            if hmonitor is not None:
                args["hmonitor"] = str(hmonitor)
            if device_name is not None:
                args["device_name"] = str(device_name)
            meta, payload = self._raw_request(
                "observe",
                args,
                timeout_sec=(timeout_ms / 1000.0) + 3.0,
            )
            return meta, payload

    def inspect(
        self,
        max_depth: int = 8,
        max_elements: int = 500,
        timeout_ms: int = 2000,
    ) -> Tuple[List[Dict[str, Any]], bool]:
        res = self.request(
            "inspect",
            {"max_depth": max_depth, "max_elements": max_elements, "timeout_ms": timeout_ms},
            timeout_sec=(timeout_ms / 1000.0) + 3.0,
        )
        return res.get("elements", []), res.get("truncated", False)

    def uia_action(
        self,
        token: str,
        action: str,
        value: Optional[str] = None,
        timeout_sec: float = 5.0,
    ) -> Dict[str, Any]:
        return self.request(
            "uia_action",
            {"token": token, "action": action, "value": value},
            timeout_sec=timeout_sec,
        )

    def click(
        self,
        observation_id: int,
        target_bbox_frame_px: List[int],
        max_age_ms: int = 500,
        dry_run: bool = False,
        timeout_sec: float = 5.0,
    ) -> Dict[str, Any]:
        return self.request(
            "click",
            {
                "observation_id": observation_id,
                "target_bbox_frame_px": target_bbox_frame_px,
                "max_age_ms": max_age_ms,
                "dry_run": dry_run,
            },
            timeout_sec=timeout_sec,
        )

    def __enter__(self) -> WcuClient:
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()
