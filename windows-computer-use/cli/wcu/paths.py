"""Filesystem locations shared by the CLI and the session process.

The package lives at ``windows-computer-use/cli/wcu`` in a source checkout,
so ``parents[2]`` is the ``windows-computer-use`` repository root. An
editable install keeps that layout, which is what makes the sibling
``client/`` and ``engine/`` directories reachable without copying them.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def repo_root() -> Path:
    """The ``windows-computer-use`` directory that holds ``client/`` and ``engine/``."""
    return Path(__file__).resolve().parents[2]


def client_dir() -> Path:
    return repo_root() / "client"


def cli_dir() -> Path:
    """The directory that contains the ``wcu`` package itself."""
    return Path(__file__).resolve().parents[1]


def default_engine_path() -> Path:
    """Locate the engine binary: ``WCU_ENGINE_PATH``, then release, then debug."""
    env = os.environ.get("WCU_ENGINE_PATH")
    if env:
        return Path(env)
    root = repo_root()
    for candidate in (
        root / "engine" / "target" / "release" / "wcu-engine.exe",
        root / "engine" / "target" / "debug" / "wcu-engine.exe",
    ):
        if candidate.exists():
            return candidate
    # Fall back to the release path so error messages name the expected location.
    return root / "engine" / "target" / "release" / "wcu-engine.exe"


def wcu_home() -> Path:
    """Root directory for session state and screenshots."""
    base = os.environ.get("WCU_HOME")
    if base:
        return Path(base)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "wcu"
    return Path.home() / ".wcu"


def state_file() -> Path:
    return wcu_home() / "session.json"


def session_dir(session_id: str) -> Path:
    return wcu_home() / "sessions" / session_id


def shots_dir(session_id: str) -> Path:
    return session_dir(session_id) / "shots"


def request_pipe_name(session_id: str) -> str:
    return rf"\\.\pipe\wcu-req-{session_id}"


def cancel_pipe_name(session_id: str) -> str:
    return rf"\\.\pipe\wcu-cancel-{session_id}"
