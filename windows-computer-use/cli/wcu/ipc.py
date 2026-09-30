"""Named-pipe IPC between the CLI and the persistent session process.

The session process owns a message-mode request pipe and a message-mode
cancel pipe. Message mode gives each write a natural frame, so no length
prefixing is needed. A client that disconnects mid-operation surfaces as a
broken pipe on the server, which is how cancellation stays responsive.

This is local IPC on the interactive desktop: no network listener, no
service, no hosted endpoint.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

import win32file
import win32pipe
import pywintypes

# Largest single message we accept. Bounded responses (window lists,
# inspection results, dispatch evidence) are far below this.
MESSAGE_BUFFER = 1024 * 1024

ERROR_MORE_DATA = 234


class PipeError(Exception):
    """A transport-level failure: the pipe could not be used."""

    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


def call(pipe_name: str, request: Dict[str, Any], timeout_sec: float) -> Dict[str, Any]:
    """Send one request on a message-mode pipe and return the response object.

    Raises ``PipeError`` for transport failures (timeout, broken pipe,
    oversized message). A well-formed response with ``ok: false`` is
    returned, not raised: the caller decides how to map operation errors.
    """
    handle = None
    try:
        handle = win32file.CreateFile(
            pipe_name,
            win32file.GENERIC_READ | win32file.GENERIC_WRITE,
            0,
            None,
            win32file.OPEN_EXISTING,
            0,
            None,
        )
        # Match the server's message mode so reads return whole messages.
        try:
            win32pipe.SetNamedPipeHandleState(
                handle, win32pipe.PIPE_READMODE_MESSAGE, None, None
            )
        except pywintypes.error:
            pass  # Already in message mode, or a byte-mode peer; reads still work.

        body = json.dumps(request).encode("utf-8")
        _write_all(handle, body)

        deadline = time.monotonic() + timeout_sec
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PipeError(
                    "timeout", f"No response within {timeout_sec:.1f}s"
                )
            try:
                hr, data = win32file.ReadFile(handle, MESSAGE_BUFFER)
            except pywintypes.error as e:
                # A disconnected peer surfaces here; treat it as a timeout
                # because the operation outcome is unknown.
                raise PipeError("broken_pipe", f"Pipe read failed: {e}")
            if hr == ERROR_MORE_DATA:
                raise PipeError(
                    "response_too_large",
                    "Response exceeded the 1 MiB message bound",
                )
            if hr != 0:
                raise PipeError("pipe_error", f"ReadFile failed with code {hr}")
            if not data:
                continue  # Zero-length message; read the next one.
            return json.loads(data.decode("utf-8"))
    except pywintypes.error as e:
        raise PipeError("connect_failed", f"Could not connect to {pipe_name}: {e}")
    finally:
        if handle is not None:
            try:
                win32file.CloseHandle(handle)
            except Exception:
                pass


def _write_all(handle, body: bytes) -> None:
    """Write the whole request as one message, respecting the deadline."""
    try:
        win32file.WriteFile(handle, body)
    except pywintypes.error as e:
        raise PipeError("write_failed", f"Pipe write failed: {e}")


def make_message(payload: Dict[str, Any]) -> bytes:
    return json.dumps(payload).encode("utf-8")


def parse_message(data: bytes) -> Dict[str, Any]:
    return json.loads(data.decode("utf-8"))
