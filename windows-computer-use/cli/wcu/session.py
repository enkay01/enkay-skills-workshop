"""Session lifecycle for the CLI: state file, start/stop/status, dispatch.

One active tool session per Windows user desktop. The state file under
``%LOCALAPPDATA%\\wcu`` records the session id, the server process id, and
the pipe names; every command discovers the active session from it. A
recorded session whose process is gone is stale: it is cleaned up and a
new one may be started.
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from wcu import ipc
from wcu.paths import (
    cancel_pipe_name,
    cli_dir,
    default_engine_path,
    request_pipe_name,
    session_dir,
    shots_dir,
    state_file,
)

START_TIMEOUT_SEC = 15.0
STOP_TIMEOUT_SEC = 10.0


class SessionError(Exception):
    """A session-level failure the CLI can report in the result envelope."""

    def __init__(self, code: str, message: str, details: Any = None):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.details = details


def _pid_alive(pid: int) -> bool:
    if not pid or pid <= 0:
        return False
    kernel32 = ctypes.windll.kernel32
    # PROCESS_QUERY_LIMITED_INFORMATION
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong(0)
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        # STILL_ACTIVE
        return exit_code.value == 259
    finally:
        kernel32.CloseHandle(handle)


def read_state() -> Optional[Dict[str, Any]]:
    try:
        with open(state_file(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _write_state(state: Dict[str, Any]) -> None:
    state_file().parent.mkdir(parents=True, exist_ok=True)
    tmp = state_file().with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, state_file())


def _remove_state() -> None:
    try:
        os.remove(state_file())
    except OSError:
        pass


def _cleanup_session_dir(session_id: str) -> None:
    """Delete a session's screenshots and directory tree."""
    import shutil

    root = session_dir(session_id)
    if not root.exists():
        return
    try:
        shutil.rmtree(root)
    except OSError:
        pass


def _cleanup_stale(state: Dict[str, Any]) -> None:
    _remove_state()
    _cleanup_session_dir(str(state.get("session_id", "")))


def _server_env() -> Dict[str, str]:
    """Environment for the session server: make the package importable.

    The server is launched with ``python -m wcu.session_server``. Prepending
    the cli directory to PYTHONPATH lets it run from a source checkout
    without requiring an installation step first.
    """
    env = dict(os.environ)
    cli = str(cli_dir())
    existing = env.get("PYTHONPATH", "")
    parts = [cli] + [p for p in existing.split(os.pathsep) if p and p != cli]
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def start_session(engine_path: Optional[Path] = None) -> Dict[str, Any]:
    """Start the persistent session process, or refuse a second one."""
    state = read_state()
    if state and _pid_alive(int(state.get("pid", 0) or 0)):
        raise SessionError(
            "session_already_active",
            "A session is already active for this desktop",
            {
                "session_id": state.get("session_id"),
                "pid": state.get("pid"),
            },
        )
    if state:
        _cleanup_stale(state)

    session_id = uuid.uuid4().hex[:16]
    engine = str(engine_path or default_engine_path())
    if not Path(engine).exists():
        raise SessionError(
            "engine_not_found",
            f"Engine binary not found at '{engine}'. "
            "Run `cargo build --release` in the engine directory.",
        )

    sdir = session_dir(session_id)
    sdir.mkdir(parents=True, exist_ok=True)
    log_path = sdir / "server.log"

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "wcu.session_server",
            "--session-id",
            session_id,
            "--engine-path",
            engine,
        ],
        env=_server_env(),
        stdout=open(log_path, "wb"),
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        close_fds=True,
    )

    deadline = time.monotonic() + START_TIMEOUT_SEC
    while time.monotonic() < deadline:
        state = read_state()
        if state and state.get("session_id") == session_id:
            if state.get("error"):
                _cleanup_stale(state)
                raise SessionError(
                    state.get("error", "session_start_failed"),
                    state.get("error_message", "Session server failed to start"),
                )
            return state
        if proc.poll() is not None:
            _cleanup_session_dir(session_id)
            detail = ""
            try:
                detail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
            except OSError:
                pass
            raise SessionError(
                "session_start_failed",
                f"Session server exited with code {proc.returncode}",
                {"stderr": detail} if detail else None,
            )
        time.sleep(0.05)

    _cleanup_session_dir(session_id)
    raise SessionError(
        "session_start_failed", "Session server did not become ready in time"
    )


def stop_session() -> Dict[str, Any]:
    """Stop the active session, release the engine, and clean up screenshots."""
    state = read_state()
    if not state:
        return {"status": "no_session"}
    pid = int(state.get("pid", 0) or 0)
    if not _pid_alive(pid):
        _cleanup_stale(state)
        return {"status": "no_session"}

    try:
        ipc.call(
            str(state["request_pipe"]),
            {"v": 1, "id": 1, "op": "stop", "args": {}},
            timeout_sec=STOP_TIMEOUT_SEC,
        )
    except ipc.PipeError:
        pass  # Server may already be gone; cleanup below still applies.

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and _pid_alive(pid):
        time.sleep(0.05)
    if _pid_alive(pid):
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=10
        )
    session_id = str(state.get("session_id", ""))
    _cleanup_stale(state)
    return {"status": "stopped", "session_id": session_id}


def require_session() -> Dict[str, Any]:
    """Return the active session state or raise ``SessionError``."""
    state = read_state()
    if not state:
        raise SessionError(
            "no_session", "No active session. Run `wcu session start` first."
        )
    pid = int(state.get("pid", 0) or 0)
    if not _pid_alive(pid):
        _cleanup_stale(state)
        raise SessionError(
            "session_dead",
            "The session process is no longer running. "
            "Run `wcu session start` to start a new one.",
        )
    return state


def session_call(
    op: str, args: Optional[Dict[str, Any]] = None, timeout_sec: float = 30.0
) -> Dict[str, Any]:
    """Send a command to the active session and return the raw response."""
    state = require_session()
    request = {"v": 1, "id": 1, "op": op, "args": args or {}}
    return ipc.call(str(state["request_pipe"]), request, timeout_sec=timeout_sec)


def cancel() -> Dict[str, Any]:
    """Ask the session to cancel any pending operation."""
    state = require_session()
    request = {"v": 1, "id": 1, "op": "cancel", "args": {}}
    return ipc.call(str(state["cancel_pipe"]), request, timeout_sec=5.0)
