"""Persistent session process: owns the engine and serves CLI commands.

One session process per Windows user desktop. It holds the ``WcuClient``
(and therefore the Rust engine) across independent CLI invocations, so
capture state, attachments, and observation identity survive between
commands. Operations are serialized; a cancel on the separate cancel pipe
terminates the engine so a pending operation fails fast instead of hanging.

The server writes the state file only after the engine is up, so
``wcu session start`` fails fast when the engine cannot start.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

# Make the repo's client package importable (repo layout: cli/wcu -> ../..).
from wcu.paths import (
    cancel_pipe_name,
    client_dir,
    request_pipe_name,
    session_dir,
    shots_dir,
    state_file,
)

_client = str(client_dir())
if _client not in sys.path:
    sys.path.insert(0, _client)

from wcu_client import WcuClient, WcuError  # noqa: E402

from wcu import ipc  # noqa: E402
from wcu.constants import REFUSAL_CODES  # noqa: E402
from wcu.history import History  # noqa: E402
from wcu.imaging import save_observation_png  # noqa: E402


def _pid_alive(pid: int) -> bool:
    import ctypes

    if not pid or pid <= 0:
        return False
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong(0)
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == 259
    finally:
        kernel32.CloseHandle(handle)


def ok_response(request: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "v": 1,
        "id": request.get("id", 0),
        "ok": True,
        "result": result,
    }


def error_response(
    request: Dict[str, Any],
    code: str,
    message: str,
    details: Any = None,
) -> Dict[str, Any]:
    err: Dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        err["details"] = details
    return {
        "v": 1,
        "id": request.get("id", 0),
        "ok": False,
        "error": err,
    }


class SessionServer:
    def __init__(self, session_id: str, engine_path: str):
        self.session_id = session_id
        self.engine_path = engine_path
        self.client: Optional[WcuClient] = None
        self.last_attach: Optional[Dict[str, Any]] = None
        self.history = History()
        self.cancel_event = threading.Event()
        self.in_flight = False
        self._shutdown = False
        self._op_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Engine lifecycle
    # ------------------------------------------------------------------

    def ensure_engine(self) -> WcuClient:
        """Return a live engine client, restarting and re-attaching if needed."""
        if self.client is not None:
            pid = self.client.pid
            if pid and _pid_alive(pid):
                return self.client
            # Engine died; drop it and start a fresh one.
            try:
                self.client.stop()
            except Exception:
                pass
            self.client = None
        self.client = WcuClient(self.engine_path)
        self.client.start()
        if self.last_attach:
            self.client.attach(**self.last_attach)
        return self.client

    # ------------------------------------------------------------------
    # Observation helpers
    # ------------------------------------------------------------------

    def _observe_and_save(self, monitor: bool = False) -> Dict[str, Any]:
        client = self.ensure_engine()
        if monitor:
            meta, payload = client.observe_monitor()
        else:
            meta, payload = client.observe()
        path = save_observation_png(shots_dir(self.session_id), meta, payload)
        meta = dict(meta)
        meta["image_path"] = str(path)
        return meta

    def _find_window(self, hwnd: str) -> Dict[str, Any]:
        client = self.ensure_engine()
        windows = client.list_windows()
        match = next(
            (w for w in windows if str(w.get("hwnd")) == str(hwnd)), None
        )
        if match is None:
            raise WcuError(
                "window_not_found", f"No visible window with hwnd {hwnd}"
            )
        return match

    def _record_attach(self, window: Dict[str, Any]) -> None:
        self.last_attach = {
            "hwnd": window["hwnd"],
            "pid": window["pid"],
            "process_create_time_utc": window.get(
                "process_create_time_utc", "unknown"
            ),
        }

    # ------------------------------------------------------------------
    # Operation handlers
    # ------------------------------------------------------------------

    def op_ping(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": "ok", "session_id": self.session_id}

    def op_capabilities(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return self.ensure_engine().doctor()

    def op_windows(self, args: Dict[str, Any]) -> Dict[str, Any]:
        client = self.ensure_engine()
        windows = client.list_windows()
        filter_text = args.get("filter")
        if filter_text:
            needle = str(filter_text).lower()
            windows = [
                w for w in windows if needle in str(w.get("title", "")).lower()
            ]
        return {"windows": windows, "count": len(windows)}

    def op_monitors(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return {"monitors": self.ensure_engine().list_monitors()}

    def op_attach(self, args: Dict[str, Any]) -> Dict[str, Any]:
        client = self.ensure_engine()
        window = self._find_window(args.get("hwnd", ""))
        self._record_attach(window)
        return client.attach(
            window["hwnd"],
            window["pid"],
            window.get("process_create_time_utc", "unknown"),
        )

    def op_focus(self, args: Dict[str, Any]) -> Dict[str, Any]:
        client = self.ensure_engine()
        window = self._find_window(args.get("hwnd", ""))
        return client.focus_window(
            window["hwnd"],
            pid=window["pid"],
            process_create_time_utc=window.get("process_create_time_utc"),
        )

    def op_switch(self, args: Dict[str, Any]) -> Dict[str, Any]:
        client = self.ensure_engine()
        window = self._find_window(args.get("hwnd", ""))
        focus = client.focus_window(
            window["hwnd"],
            pid=window["pid"],
            process_create_time_utc=window.get("process_create_time_utc"),
        )
        # Re-resolve after the focus change: restoring a minimized window can
        # change its identity fields.
        window = self._find_window(args.get("hwnd", ""))
        self._record_attach(window)
        attach = client.attach(
            window["hwnd"],
            window["pid"],
            window.get("process_create_time_utc", "unknown"),
        )
        observation = self._observe_and_save()
        return {
            "focus": focus,
            "attach": attach,
            "observation": observation,
        }

    def op_observe(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return self._observe_and_save(monitor=bool(args.get("monitor")))

    def op_inspect(self, args: Dict[str, Any]) -> Dict[str, Any]:
        client = self.ensure_engine()
        elements, truncated = client.inspect(
            max_depth=int(args.get("max_depth", 8)),
            max_elements=int(args.get("max_elements", 500)),
            timeout_ms=int(args.get("timeout_ms", 2000)),
        )
        result: Dict[str, Any] = {
            "elements": elements,
            "truncated": truncated,
            "count": len(elements),
        }
        question = args.get("question")
        if question:
            result["question"] = str(question)
            # Record the question so history shows what each inspection served.
            self.history.record("inspect", "ok", question=str(question))
        return result

    def op_history(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return {"entries": self.history.entries()}

    def op_status(self, args: Dict[str, Any]) -> Dict[str, Any]:
        engine_pid = self.client.pid if self.client else None
        engine_alive = bool(engine_pid and _pid_alive(engine_pid))
        target = None
        if self.last_attach and engine_alive:
            try:
                windows = self.client.list_windows()
                match = next(
                    (
                        w
                        for w in windows
                        if str(w.get("hwnd")) == str(self.last_attach["hwnd"])
                    ),
                    None,
                )
                if match:
                    target = {
                        "hwnd": match["hwnd"],
                        "title": match.get("title", ""),
                        "pid": match["pid"],
                    }
            except Exception:
                target = dict(self.last_attach)
        return {
            "session_id": self.session_id,
            "engine_pid": engine_pid,
            "engine_alive": engine_alive,
            "attached_target": target,
            "in_flight": self.in_flight,
            "history_entries": len(self.history.entries()),
        }

    def op_act(self, args: Dict[str, Any]) -> Dict[str, Any]:
        client = self.ensure_engine()
        action = str(args.get("action", ""))
        dry_run = bool(args.get("dry_run"))
        obs_id = args.get("observation_id")
        obs_id = int(obs_id) if obs_id is not None else None
        max_age = int(args.get("max_age_ms", 500))

        try:
            dispatch = self._dispatch_action(client, action, args, obs_id, max_age, dry_run)
        except WcuError as e:
            if self.cancel_event.is_set():
                self.cancel_event.clear()
                raise WcuError("cancelled", "Operation cancelled") from e
            if e.code in REFUSAL_CODES and not dry_run:
                # The proposal no longer applies. Return updated evidence so
                # the caller can reconsider without redundant setup. The
                # evidence rides in the error details; the envelope is ok:false.
                evidence = self._observe_and_save()
                self.history.record(
                    f"act:{action}",
                    "refused",
                    observation_id=obs_id,
                    error_code=e.code,
                )
                raise WcuError(
                    e.code,
                    e.message,
                    {"evidence": evidence, "action": action},
                ) from e
            raise

        outcome = "dry_run" if dry_run else "ok"
        self.history.record(
            f"act:{action}", outcome, observation_id=obs_id
        )
        result: Dict[str, Any] = {
            "status": outcome,
            "action": action,
            "dispatch": dispatch,
        }
        if not dry_run:
            # Post-action evidence so the caller can choose the next step.
            result["evidence"] = self._observe_and_save()
        return result

    def _dispatch_action(
        self,
        client: WcuClient,
        action: str,
        args: Dict[str, Any],
        obs_id: Optional[int],
        max_age: int,
        dry_run: bool,
    ) -> Dict[str, Any]:
        if action == "click":
            if args.get("bbox"):
                target = {"bbox_frame_px": [int(v) for v in args["bbox"]]}
            elif args.get("point"):
                target = {"point_frame_px": [int(v) for v in args["point"]]}
            else:
                raise WcuError("invalid_request", "click needs bbox or point")
            return client.click(
                observation_id=obs_id,
                target=target,
                max_age_ms=max_age,
                dry_run=dry_run,
                button=str(args.get("button", "left")),
                click_count=int(args.get("click_count", 1)),
            )
        if action == "type":
            return client.type_text(
                text=str(args.get("text", "")),
                observation_id=obs_id,
                max_age_ms=max_age,
                delay_ms=args.get("delay_ms"),
                method=str(args.get("method", "unicode")),
                dry_run=dry_run,
            )
        if action == "press":
            return client.press_key(
                chord=str(args.get("chord", "")),
                observation_id=obs_id,
                max_age_ms=max_age,
                dry_run=dry_run,
            )
        if action == "scroll":
            target = None
            if args.get("point"):
                target = {"point_frame_px": [int(v) for v in args["point"]]}
            return client.scroll(
                notches_x=int(args.get("notches_x", 0)),
                notches_y=int(args.get("notches_y", 0)),
                observation_id=obs_id,
                max_age_ms=max_age,
                dry_run=dry_run,
                target=target,
            )
        if action == "hover":
            if args.get("point"):
                target = {"point_frame_px": [int(v) for v in args["point"]]}
            elif args.get("bbox"):
                target = {"bbox_frame_px": [int(v) for v in args["bbox"]]}
            else:
                raise WcuError("invalid_request", "hover needs point or bbox")
            return client.hover(
                observation_id=obs_id,
                target=target,
                max_age_ms=max_age,
                dry_run=dry_run,
                duration_ms=args.get("duration_ms"),
            )
        if action == "drag":
            if not args.get("from") or not args.get("to"):
                raise WcuError("invalid_request", "drag needs from and to points")
            return client.drag(
                from_target={"point_frame_px": [int(v) for v in args["from"]]},
                to_target={"point_frame_px": [int(v) for v in args["to"]]},
                observation_id=obs_id,
                max_age_ms=max_age,
                dry_run=dry_run,
                steps=args.get("steps"),
                duration_ms=args.get("duration_ms"),
            )
        if action == "focus":
            window = self._find_window(args.get("hwnd", ""))
            return client.focus_window(
                window["hwnd"],
                pid=window["pid"],
                process_create_time_utc=window.get("process_create_time_utc"),
            )
        if action == "invoke":
            return client.uia_action(
                token=str(args.get("token", "")), action="invoke"
            )
        if action == "set-value":
            return client.uia_action(
                token=str(args.get("token", "")),
                action="set_value",
                value=args.get("value"),
            )
        raise WcuError("invalid_request", f"Unknown action '{action}'")

    def op_stop(self, args: Dict[str, Any]) -> Dict[str, Any]:
        self._shutdown = True
        if self.client is not None:
            try:
                self.client.stop()
            except Exception:
                pass
            self.client = None
        return {"status": "stopped", "session_id": self.session_id}

    # ------------------------------------------------------------------
    # Request dispatch
    # ------------------------------------------------------------------

    # Operations that only read local session state and never touch the engine.
    # They do not count as "in flight" for cancellation purposes.
    LOCAL_OPS = {"ping", "status", "history", "stop"}

    # Operations that record their own history entries (with richer detail
    # than the generic success record).
    SELF_RECORDING_OPS = {"act"}

    def handle(self, request: Dict[str, Any]) -> Dict[str, Any]:
        op = str(request.get("op", ""))
        args = request.get("args") or {}
        handler = getattr(self, f"op_{op}", None)
        if handler is None:
            return error_response(
                request, "unsupported", f"Unknown operation '{op}'"
            )
        is_local = op in self.LOCAL_OPS
        # A cancel that arrived while no operation was pending is a no-op;
        # clear it here so it never poisons the next command.
        self.cancel_event.clear()
        try:
            with self._op_lock:
                self.in_flight = not is_local
                try:
                    result = handler(args)
                finally:
                    self.in_flight = False
            if not is_local and op not in self.SELF_RECORDING_OPS:
                self.history.record(op, "ok")
            return ok_response(request, result)
        except WcuError as e:
            status = "refused" if e.code in REFUSAL_CODES else "error"
            self.history.record(
                op, status, error_code=e.code
            )
            return error_response(request, e.code, e.message, e.details)
        except Exception as e:  # noqa: BLE001 - report honestly, never crash
            self.history.record(op, "error", error_code="internal_error")
            return error_response(
                request,
                "internal_error",
                f"{type(e).__name__}: {e}",
                {"traceback": traceback.format_exc(limit=5)},
            )

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    def _do_cancel(self) -> bool:
        """Cancel a pending operation. Returns True if one was interrupted."""
        was_in_flight = self.in_flight
        if was_in_flight and self.client is not None:
            # Kill the engine directly: the pending operation holds the
            # client lock, so going through client.stop() would block until
            # the operation finished. Terminating the process unblocks the
            # pending read immediately.
            self.client.kill()
            self.cancel_event.set()
        return was_in_flight

    def _cancel_listener(self, cancel_handle) -> None:
        import win32file
        import win32pipe
        import pywintypes

        while not self._shutdown:
            try:
                win32pipe.ConnectNamedPipe(cancel_handle, None)
            except pywintypes.error:
                if self._shutdown:
                    break
                continue
            try:
                hr, data = win32file.ReadFile(cancel_handle, 256)
                if hr == 0 and data:
                    cancelled = self._do_cancel()
                    reply = ipc.make_message(
                        {"v": 1, "id": 0, "ok": True,
                         "result": {"cancelled": cancelled}}
                    )
                    try:
                        win32file.WriteFile(cancel_handle, reply)
                    except pywintypes.error:
                        pass
            except pywintypes.error:
                if self._shutdown:
                    break
            except Exception:
                if self._shutdown:
                    break

    # ------------------------------------------------------------------
    # Serving
    # ------------------------------------------------------------------

    def _create_request_pipe(self):
        import win32pipe

        return win32pipe.CreateNamedPipe(
            request_pipe_name(self.session_id),
            win32pipe.PIPE_ACCESS_DUPLEX,
            win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE | win32pipe.PIPE_WAIT,
            win32pipe.PIPE_UNLIMITED_INSTANCES,
            ipc.MESSAGE_BUFFER,
            ipc.MESSAGE_BUFFER,
            0,
            None,
        )

    def _create_cancel_pipe(self):
        import win32pipe

        return win32pipe.CreateNamedPipe(
            cancel_pipe_name(self.session_id),
            win32pipe.PIPE_ACCESS_DUPLEX,
            win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE | win32pipe.PIPE_WAIT,
            win32pipe.PIPE_UNLIMITED_INSTANCES,
            4096,
            4096,
            0,
            None,
        )

    def _write_state(self) -> None:
        state = {
            "session_id": self.session_id,
            "pid": os.getpid(),
            "request_pipe": request_pipe_name(self.session_id),
            "cancel_pipe": cancel_pipe_name(self.session_id),
            "created_at": time.time(),
            "engine_path": self.engine_path,
        }
        state_file().parent.mkdir(parents=True, exist_ok=True)
        tmp = state_file().with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, state_file())

    def serve(self) -> int:
        import win32file
        import win32pipe
        import pywintypes

        # Fail fast: bring the engine up before advertising the session.
        try:
            self.ensure_engine()
        except Exception as e:
            _write_error_state(
                self.session_id,
                "engine_start_failed",
                f"{type(e).__name__}: {e}",
            )
            return 1

        self._write_state()

        cancel_handle = self._create_cancel_pipe()
        cancel_thread = threading.Thread(
            target=self._cancel_listener,
            args=(cancel_handle,),
            daemon=True,
        )
        cancel_thread.start()

        try:
            while not self._shutdown:
                req_handle = self._create_request_pipe()
                try:
                    win32pipe.ConnectNamedPipe(req_handle, None)
                except pywintypes.error:
                    win32file.CloseHandle(req_handle)
                    if self._shutdown:
                        break
                    continue

                response = None
                try:
                    hr, data = win32file.ReadFile(req_handle, ipc.MESSAGE_BUFFER)
                    if hr == ipc.ERROR_MORE_DATA:
                        response = error_response(
                            {}, "response_too_large",
                            "Request exceeded the 1 MiB message bound",
                        )
                    elif hr != 0:
                        # Client disconnected before sending; wait for the next.
                        continue
                    elif not data:
                        continue
                    else:
                        request = ipc.parse_message(data)
                        response = self.handle(request)
                except pywintypes.error:
                    # Client went away; nothing to respond to.
                    continue
                except Exception as e:  # noqa: BLE001
                    response = error_response(
                        {}, "internal_error", f"{type(e).__name__}: {e}"
                    )

                if response is not None:
                    try:
                        win32file.WriteFile(
                            req_handle, ipc.make_message(response)
                        )
                    except pywintypes.error:
                        pass
                try:
                    win32file.CloseHandle(req_handle)
                except Exception:
                    pass
        finally:
            self._shutdown = True
            try:
                win32file.CloseHandle(cancel_handle)
            except Exception:
                pass
            if self.client is not None:
                try:
                    self.client.stop()
                except Exception:
                    pass
            # Release session resources: screenshots and state.
            import shutil

            root = session_dir(self.session_id)
            if root.exists():
                try:
                    shutil.rmtree(root)
                except OSError:
                    pass
            try:
                os.remove(state_file())
            except OSError:
                pass
        return 0


def _write_error_state(session_id: str, code: str, message: str) -> None:
    state = {
        "session_id": session_id,
        "error": code,
        "error_message": message,
    }
    try:
        state_file().parent.mkdir(parents=True, exist_ok=True)
        with open(state_file(), "w", encoding="utf-8") as f:
            json.dump(state, f)
    except OSError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="wcu session server")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--engine-path", required=True)
    args = parser.parse_args()
    server = SessionServer(args.session_id, args.engine_path)
    return server.serve()


if __name__ == "__main__":
    raise SystemExit(main())
