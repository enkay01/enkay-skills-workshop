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
from typing import Any, Dict, List, Optional, Tuple, Union


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
        self._stopping = False

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
        # _raw_request calls back into this method when the engine fails to
        # answer. Without this guard, a shutdown that itself times out would
        # recurse until the stack ran out.
        if self._stopping:
            return
        proc = self._proc
        self._stopping = True
        try:
            if proc.poll() is None:
                # Attempt clean shutdown request if pipes are open
                try:
                    self._raw_request("shutdown", {}, timeout_sec=1.0)
                except Exception:
                    pass
                try:
                    proc.terminate()
                    proc.wait(timeout=2.0)
                except Exception:
                    if proc.poll() is None:
                        proc.kill()
        finally:
            self._stopping = False
            self._proc = None
            self._is_alive = False

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def kill(self) -> None:
        """Terminate the engine process immediately, without waiting.

        Used by cancellation. A pending operation holds ``self._lock`` for
        its whole duration, so going through ``stop()`` would block until
        that operation finished -- exactly what cancellation must avoid.
        Terminating the process closes the pipes and unblocks the pending
        read at once, so the in-flight operation fails fast and reports.
        """
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

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

                # The engine echoes the request id. A mismatch means this
                # response belongs to an earlier request (stale) or was
                # misrouted; never let it satisfy the current one.
                resp_id = resp_obj.get("id")
                if resp_id is not None and resp_id != req_id:
                    raise WcuError(
                        "stale_response",
                        f"Response id {resp_id} does not match request id {req_id}",
                    )

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

    def focus_window(
        self,
        hwnd: str | int,
        attach: bool = False,
        pid: Optional[int] = None,
        process_create_time_utc: Optional[str] = None,
        timeout_sec: float = 5.0,
    ) -> Dict[str, Any]:
        """Bring a window to the front and verify the result.

        The engine restores the window if minimized, requests the foreground, and
        then verifies against the actual foreground window rather than trusting
        the request. A window that refuses foreground raises `focus_refused` and
        names the window that actually holds it.
        """
        args: Dict[str, Any] = {"hwnd": str(hwnd)}
        if pid is not None:
            args["pid"] = pid
        if process_create_time_utc is not None:
            args["process_create_time_utc"] = process_create_time_utc
        result = self.request("focus_window", args, timeout_sec=timeout_sec)
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

    def switch_window(
        self,
        hwnd: str | int,
        pid: Optional[int] = None,
        process_create_time_utc: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Focus a window, re-attach the engine to it, and observe it afresh.

        The engine owns exactly one capture target, and focusing a window does not
        by itself move that target. Composing the three steps here means a caller
        cannot accidentally focus one window and keep observing another. A fresh
        observation is required afterwards, because the engine cleared the previous
        window's observation when the focus changed.
        """
        focus = self.focus_window(
            hwnd,
            pid=pid,
            process_create_time_utc=process_create_time_utc,
        )
        windows = self.list_windows()
        matching = [w for w in windows if str(w.get("hwnd")) == str(hwnd)]
        if not matching:
            raise WcuError(
                "window_gone",
                f"Focused window {hwnd} is no longer listed",
                focus,
            )
        target = matching[0]
        attach = self.attach(
            target["hwnd"],
            target["pid"],
            target["process_create_time_utc"],
        )
        meta, _payload = self.observe()
        return {
            "focus": focus,
            "attach": attach,
            "observation_id": meta.get("observation_id"),
            "window_identity": meta.get("window_identity"),
        }

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

    @staticmethod
    def target(
        bbox_frame_px: Optional[List[int]] = None,
        point_frame_px: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        """Build the one uniform pointer target argument every pointer action accepts.

        A target is expressed in the pixels of the observation the caller saw:
        either a bounding box, whose centre is used, or a point. A bare screen
        coordinate is deliberately not expressible, for the same reason the
        original click did not accept one.
        """
        if bbox_frame_px is not None and point_frame_px is not None:
            raise ValueError("Pass either bbox_frame_px or point_frame_px, not both")
        if bbox_frame_px is not None:
            if len(bbox_frame_px) != 4:
                raise ValueError("bbox_frame_px must be [x, y, w, h]")
            return {"bbox_frame_px": [int(v) for v in bbox_frame_px]}
        if point_frame_px is not None:
            if len(point_frame_px) != 2:
                raise ValueError("point_frame_px must be [x, y]")
            return {"point_frame_px": [int(v) for v in point_frame_px]}
        raise ValueError("A pointer action needs bbox_frame_px or point_frame_px")

    def click(
        self,
        observation_id: int,
        target_bbox_frame_px: Optional[List[int]] = None,
        max_age_ms: int = 30000,
        dry_run: bool = False,
        timeout_sec: float = 5.0,
        *,
        target: Optional[Dict[str, Any]] = None,
        button: str = "left",
        click_count: int = 1,
    ) -> Dict[str, Any]:
        """Dispatch a click through the shared guard.

        `target_bbox_frame_px` is the original spelling and is unchanged. A caller
        that prefers the uniform argument can pass `target` instead. Both
        `button` and `click_count` default to the already verified single left
        click, and every variant runs the identical guard.
        """
        args: Dict[str, Any] = {
            "observation_id": observation_id,
            "max_age_ms": max_age_ms,
            "dry_run": dry_run,
            "button": button,
            "click_count": click_count,
        }
        if target is not None:
            args["target"] = target
        elif target_bbox_frame_px is not None:
            args["target_bbox_frame_px"] = list(target_bbox_frame_px)
        else:
            raise ValueError("click needs target_bbox_frame_px or target")
        return self.request("click", args, timeout_sec=timeout_sec)

    def type_text(
        self,
        text: str,
        observation_id: Optional[int] = None,
        max_age_ms: int = 500,
        delay_ms: Optional[int] = None,
        dry_run: bool = False,
        timeout_sec: float = 20.0,
        method: str = "unicode",
    ) -> Dict[str, Any]:
        """Type text: unicode key events, paste, or a single commit message.

        method="paste" delivers through the clipboard with readback verification.
        method="commit" sends one EM_REPLACESEL message to the focused editor and
        verifies by readback, without touching the clipboard or the input stream.

        Keyboard actions bind to window identity and foreground rather than to a
        point, so no pointer target is accepted here. Supplying an observation id
        additionally binds the action to that frame.
        """
        args: Dict[str, Any] = {
            "text": text,
            "max_age_ms": max_age_ms,
            "dry_run": dry_run,
        }
        if observation_id is not None:
            args["observation_id"] = observation_id
        if delay_ms is not None:
            args["delay_ms"] = delay_ms
        args["method"] = method
        return self.request("type_text", args, timeout_sec=timeout_sec)

    def press_key(
        self,
        chord: str,
        observation_id: Optional[int] = None,
        max_age_ms: int = 500,
        repeat: Optional[int] = None,
        hold_ms: Optional[int] = None,
        dry_run: bool = False,
        timeout_sec: float = 10.0,
    ) -> Dict[str, Any]:
        """Send a key chord such as `ctrl+shift+s` as a single call.

        An unrecognised key name is refused with the `unknown_key` error code
        before any event is generated.
        """
        args: Dict[str, Any] = {
            "chord": chord,
            "max_age_ms": max_age_ms,
            "dry_run": dry_run,
        }
        if observation_id is not None:
            args["observation_id"] = observation_id
        if repeat is not None:
            args["repeat"] = repeat
        if hold_ms is not None:
            args["hold_ms"] = hold_ms
        return self.request("press_key", args, timeout_sec=timeout_sec)

    def scroll(
        self,
        notches_x: int = 0,
        notches_y: int = 0,
        observation_id: Optional[int] = None,
        max_age_ms: int = 30000,
        dry_run: bool = False,
        timeout_sec: float = 5.0,
        *,
        target: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Scroll in wheel notches, optionally at one uniform pointer target.

        Without a target the wheel is sent at the current pointer position, and
        the result reports that the hit-test ownership check did not apply.
        """
        args: Dict[str, Any] = {
            "notches_x": notches_x,
            "notches_y": notches_y,
            "max_age_ms": max_age_ms,
            "dry_run": dry_run,
        }
        if observation_id is not None:
            args["observation_id"] = observation_id
        if target is not None:
            args["target"] = target
        return self.request("scroll", args, timeout_sec=timeout_sec)

    def hover(
        self,
        observation_id: int,
        target: Optional[Dict[str, Any]] = None,
        max_age_ms: int = 30000,
        dry_run: bool = False,
        duration_ms: Optional[int] = None,
        timeout_sec: float = 5.0,
        *,
        target_bbox_frame_px: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        """Move the pointer to a target without changing any button state."""
        args: Dict[str, Any] = {
            "observation_id": observation_id,
            "max_age_ms": max_age_ms,
            "dry_run": dry_run,
        }
        if target is not None:
            args["target"] = target
        elif target_bbox_frame_px is not None:
            args["target_bbox_frame_px"] = list(target_bbox_frame_px)
        else:
            raise ValueError("hover needs target or target_bbox_frame_px")
        if duration_ms is not None:
            args["duration_ms"] = duration_ms
        return self.request("hover", args, timeout_sec=timeout_sec)

    def drag(
        self,
        from_target: Dict[str, Any],
        to_target: Dict[str, Any],
        observation_id: int,
        max_age_ms: int = 30000,
        dry_run: bool = False,
        steps: Optional[int] = None,
        duration_ms: Optional[int] = None,
        timeout_sec: float = 10.0,
    ) -> Dict[str, Any]:
        """Drag from one observed-frame target to another.

        Both endpoints must lie inside the observed frame, so a drag that would
        have to leave the attached window is not expressible.
        """
        args: Dict[str, Any] = {
            "observation_id": observation_id,
            "from": from_target,
            "to": to_target,
            "max_age_ms": max_age_ms,
            "dry_run": dry_run,
        }
        if steps is not None:
            args["steps"] = steps
        if duration_ms is not None:
            args["duration_ms"] = duration_ms
        return self.request("drag", args, timeout_sec=timeout_sec)

    def capabilities(self) -> Dict[str, Any]:
        """The engine's capability report, including the action vocabulary."""
        return self.request("doctor")

    def actions(self) -> List[str]:
        """The action names this engine supports, for discovery."""
        return list(self.capabilities().get("actions", []))

    def __enter__(self) -> WcuClient:
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()
