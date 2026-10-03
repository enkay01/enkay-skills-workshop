"""Persistent repository hygiene test asserting no retired stack references exist."""

from __future__ import annotations

import pathlib
import re
import subprocess
from typing import Final

FORBIDDEN_FILE_PATTERNS: Final[tuple[str, ...]] = (
    "windows-computer-use/",  # root codebase path (skills/windows-computer-use/ is allowed)
    "tools/pyautogui-cli",
    "tools/desktop-session",
    "codex-computer-use-technical-report.md",
    "show-me-",
    "fixture_log.txt",
    "security-review.md",
)

FORBIDDEN_CONTENT_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"windows-computer-use/(?:engine|cli|client|tests|profiles|specs|research|scripts)"),
    re.compile(r"tools/pyautogui-cli"),
    re.compile(r"tools/desktop-session"),
    re.compile(r"codex-computer-use-technical-report\.md"),
    re.compile(r"\bfast-tools\b"),
    re.compile(r"\bwcu\s+(?:session|windows|switch|act|observe|inspect|help|capabilities)\b"),
    re.compile(r"\bwcu\.exe\b"),
)

ARTIFACT_EXTENSIONS: Final[tuple[str, ...]] = (
    ".png",
    ".jpg",
    ".jpeg",
    ".pyc",
    ".log",
    ".exe",
    ".dll",
)


def get_tracked_files(repo_root: pathlib.Path) -> list[str]:
    """Retrieve list of files tracked by git in the repository."""
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def test_no_removed_paths_are_tracked() -> None:
    """Assert that no tracked file matches any removed stack or tool path."""
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    tracked = get_tracked_files(repo_root)

    violations: list[str] = []
    for file_path in tracked:
        for forbidden in FORBIDDEN_FILE_PATTERNS:
            if file_path.startswith(forbidden) or file_path == forbidden:
                violations.append(f"Tracked file in forbidden path: {file_path}")

    assert not violations, "\n".join(violations)


def test_no_run_artifacts_are_tracked() -> None:
    """Assert that no images, logs, or binaries are tracked in the repository."""
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    tracked = get_tracked_files(repo_root)

    violations: list[str] = []
    for file_path in tracked:
        path_obj = pathlib.Path(file_path)
        if path_obj.suffix.lower() in ARTIFACT_EXTENSIONS:
            violations.append(f"Forbidden artifact tracked: {file_path}")

    assert not violations, "\n".join(violations)


def test_no_retired_stack_references_in_tracked_contents() -> None:
    """Assert that tracked files do not reference retired paths or binaries."""
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    tracked = get_tracked_files(repo_root)

    violations: list[str] = []
    for file_rel in tracked:
        # Skip the hygiene test itself and git configuration
        if file_rel in ("tests/test_hygiene.py", ".gitmodules"):
            continue

        file_path = repo_root / file_rel
        if not file_path.is_file():
            continue

        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

        for line_num, line in enumerate(content.splitlines(), start=1):
            for pattern in FORBIDDEN_CONTENT_PATTERNS:
                if pattern.search(line):
                    violations.append(
                        f"{file_rel}:{line_num}: contains retired reference: '{line.strip()}'"
                    )

    assert not violations, f"Found {len(violations)} retired reference violations:\n" + "\n".join(violations)
