#!/usr/bin/env python3
"""Automated release and synchronization process for Windows Computer Use (WCU).

Orchestrates the complete build, test, package, and deployment workflow:
1. Pre-flight verification (cargo, python, pip, environment)
2. Test suite validation (regression check via pytest)
3. Session lifecycle shutdown (terminates running session to unlock binaries)
4. Engine compilation (`cargo build --release`)
5. CLI package update (`pip install -e cli`)
6. Skill synchronization and linking across:
   - ~/.gemini/config/skills/windows-computer-use (Junction -> repo/skills/windows-computer-use)
   - ~/.agents/skills/windows-computer-use       (Junction -> repo/skills/windows-computer-use)
   - ~/.claude/skills/windows-computer-use       (Junction -> ~/.agents/skills/windows-computer-use)
7. Self-verification test (Session start -> Capabilities check -> Session stop)
"""

from __future__ import annotations

import argparse
import datetime
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure wcu package is importable
_script_dir = Path(__file__).resolve().parent
_wcu_root = _script_dir.parent
_cli_dir = _wcu_root / "cli"
_workspace_root = _wcu_root.parent

if str(_cli_dir) not in sys.path:
    sys.path.insert(0, str(_cli_dir))

from wcu import __version__
from wcu import installer
from wcu import session as sess
from wcu.paths import default_engine_path, repo_root


# Reconfigure stdout/stderr to utf-8 if possible
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _log(msg: str, prefix: str = "::") -> None:
    print(f"{prefix} {msg}", flush=True)


def _success(msg: str) -> None:
    print(f"[OK] {msg}", flush=True)


def _warn(msg: str) -> None:
    print(f"[WARN] {msg}", flush=True)


def _err(msg: str) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr, flush=True)


def check_status() -> int:
    """Performs a comprehensive status check across engine, session, and skills."""
    _log("WCU Status & Health Check", prefix="==")
    print(f"CLI Version:     {__version__}")
    print(f"Workspace Root:  {_workspace_root}")
    print(f"WCU Repo Root:   {_wcu_root}")

    # Check Engine
    engine_bin = default_engine_path()
    print("\n--- Engine Binary ---")
    if engine_bin.exists():
        stat = engine_bin.stat()
        mtime = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        size_mb = stat.st_size / (1024 * 1024)
        _success(f"Binary exists: {engine_bin}")
        print(f"  Modified:   {mtime}")
        print(f"  Size:       {size_mb:.2f} MB")

        # Check source timestamps vs binary
        engine_src = _wcu_root / "engine" / "src"
        stale = False
        if engine_src.is_dir():
            for rs_file in engine_src.glob("*.rs"):
                if rs_file.stat().st_mtime > stat.st_mtime:
                    stale = True
                    _warn(f"Engine binary is older than source: {rs_file.name}")
                    break
        if not stale:
            _success("Engine binary is up to date with source files.")
    else:
        _warn(f"Engine binary not found at {engine_bin}")

    # Check Session
    print("\n--- Session Status ---")
    state = sess.read_state()
    if state and sess._pid_alive(int(state.get("pid", 0) or 0)):
        _log(f"Active session running (PID {state.get('pid')}, ID {state.get('session_id')})")
    else:
        _success("No active session running (system is clean).")

    # Check Skills
    print("\n--- Skill Environments ---")
    skill_info = installer.inspect_skills()
    canonical = skill_info.get("canonical_source")
    print(f"Canonical source: {canonical or 'Not found'}")
    print(f"Packaged source:  {skill_info.get('packaged_source')}")

    for target_name, details in skill_info.get("targets", {}).items():
        path = details["path"]
        exists = details["exists"]
        kind = details["kind"]
        link_target = details.get("link_target")
        points_to_can = details.get("points_to_canonical")
        has_skill = details.get("has_skill_md")

        status_str = f"[{kind.upper()}]" if exists else "[MISSING]"
        if points_to_can:
            _success(f"{target_name:<8} {status_str} -> {link_target} (synced)")
        elif exists and kind == "junction":
            _log(f"{target_name:<8} {status_str} -> {link_target}")
        elif exists:
            _log(f"{target_name:<8} {status_str} at {path} (has SKILL.md: {has_skill})")
        else:
            _warn(f"{target_name:<8} {status_str} at {path}")

    print("\nStatus check complete.")
    return 0


def run_tests() -> bool:
    """Runs the core regression test suites."""
    _log("Step 1: Running regression test suite...")
    test_files = [
        str(_wcu_root / "tests" / "test_coordinate_translation.py"),
        str(_wcu_root / "tests" / "test_auto_refresh.py"),
        str(_wcu_root / "tests" / "test_max_age_defaults.py"),
        str(_wcu_root / "tests" / "test_cli_session.py"),
        str(_wcu_root / "tests" / "test_installer.py"),
    ]

    cmd = [sys.executable, "-m", "pytest", "-q"] + test_files
    res = subprocess.run(cmd, cwd=str(_workspace_root))
    if res.returncode != 0:
        _err("Regression test suite failed! Release aborted.")
        return False
    _success("Regression test suite passed.")
    return True


def stop_active_session() -> None:
    """Cleanly terminates any active session to unlock engine binary."""
    _log("Step 2: Stopping active session and releasing file locks...")
    state = sess.read_state()
    if state:
        sess.stop_session()
        time.sleep(0.5)

    # Ensure no lingering wcu-engine processes hold file handles on Windows
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/IM", "wcu-engine.exe"],
            capture_output=True,
        )
    _success("Session stopped and locks released.")


def build_engine(clean: bool = False) -> bool:
    """Compiles the Rust engine binary in release mode."""
    _log("Step 3: Compiling Rust engine (cargo build --release)...")
    engine_dir = _wcu_root / "engine"

    if clean:
        _log("Cleaning cargo target...")
        subprocess.run(["cargo", "clean"], cwd=str(engine_dir), check=False)

    cargo_cmd = ["cargo", "build", "--release"]
    res = subprocess.run(cargo_cmd, cwd=str(engine_dir))
    if res.returncode != 0:
        _err("Cargo engine compilation failed!")
        return False

    engine_bin = default_engine_path()
    if not engine_bin.is_file():
        _err(f"Compiled binary not found at {engine_bin}")
        return False

    stat = engine_bin.stat()
    mtime = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%H:%M:%S")
    _success(f"Engine compiled successfully: {engine_bin.name} ({stat.st_size / 1024 / 1024:.2f} MB, built at {mtime})")
    return True


def install_cli() -> bool:
    """Updates editable installation of wcu package."""
    _log("Step 4: Updating wcu CLI package (pip install -e)...")
    res = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-e", str(_cli_dir)],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        _err(f"pip install failed:\n{res.stderr}")
        return False
    _success("wcu CLI package updated.")
    return True


def sync_and_link_skills(mode: str = "link") -> bool:
    """Synchronizes packaged SKILL.md and creates junctions/copies across agent hosts."""
    _log(f"Step 5: Synchronizing skills across agent environments (mode: {mode})...")

    # 1. Update packaged SKILL.md in cli/wcu/SKILL.md from repo
    canonical = installer.find_canonical_skill_dir()
    if canonical and (canonical / "SKILL.md").is_file():
        dest = _cli_dir / "wcu" / "SKILL.md"
        shutil.copy2(str(canonical / "SKILL.md"), str(dest))
        _success(f"Packaged SKILL.md updated from {canonical.name}")

    # 2. Deploy/Link skills
    res = installer.install_skills(mode=mode)
    actions = res.get("actions", {})
    all_ok = True
    for name, action in actions.items():
        status = action.get("status")
        if status == "linked":
            _success(f"Skill '{name}': junction -> {action.get('destination')}")
        elif status == "copied":
            _success(f"Skill '{name}': copied from {action.get('source')}")
        else:
            _err(f"Skill '{name}': error {action.get('error')}")
            all_ok = False

    return all_ok


def verify_release() -> bool:
    """Performs end-to-end self-verification (session start -> capabilities -> stop)."""
    _log("Step 6: Running self-verification test (Doctor)...")
    try:
        # Start session
        start_state = sess.start_session()
        session_id = start_state.get("session_id")
        _log(f"Verification session started: {session_id}")

        # Request capabilities
        caps_resp = sess.session_call("capabilities", {})
        if not caps_resp.get("ok"):
            _err(f"Capabilities check failed: {caps_resp}")
            sess.stop_session()
            return False

        caps = caps_resp.get("result", {})
        engine_v = caps.get("engine_version", "unknown")
        _success(f"Session capabilities verified (Engine version: {engine_v})")

        # Stop session
        sess.stop_session()
        _success("Verification session stopped cleanly.")
        return True
    except Exception as e:
        _err(f"Self-verification failed: {e}")
        try:
            sess.stop_session()
        except Exception:
            pass
        return False


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="WCU Release and Synchronization Orchestrator"
    )
    parser.add_argument(
        "--skip-tests", action="store_true", help="Skip regression test suite"
    )
    parser.add_argument(
        "--copy", action="store_true", help="Use file copying instead of directory junctions"
    )
    parser.add_argument(
        "--check", action="store_true", help="Perform status check without making changes"
    )
    parser.add_argument(
        "--clean", action="store_true", help="Clean cargo build artifacts before build"
    )

    args = parser.parse_args(argv)

    if args.check:
        return check_status()

    start_time = time.monotonic()
    _log("Starting Windows Computer Use (WCU) Release Process", prefix="===")

    # 1. Tests
    if not args.skip_tests:
        if not run_tests():
            return 1
    else:
        _warn("Skipping regression tests (--skip-tests specified).")

    # 2. Stop session
    stop_active_session()

    # 3. Build engine
    if not build_engine(clean=args.clean):
        return 1

    # 4. Install CLI
    if not install_cli():
        return 1

    # 5. Sync & Link Skills
    mode = "copy" if args.copy else "link"
    if not sync_and_link_skills(mode=mode):
        return 1

    # 6. Self-Verification
    if not verify_release():
        return 1

    elapsed = time.monotonic() - start_time
    _log(f"Release process certified and completed in {elapsed:.2f}s!", prefix="===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
