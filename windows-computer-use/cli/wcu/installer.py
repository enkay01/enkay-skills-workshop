"""Skill installation and synchronization for Windows Computer Use (WCU).

Manages mounting the WCU skill into agent host skill directories:
  - Gemini / Antigravity: ~/.gemini/config/skills/windows-computer-use
  - Generic Agents: ~/.agents/skills/windows-computer-use
  - Claude Code: ~/.claude/skills/windows-computer-use

Supports two modes:
  - "link": Creates NTFS Directory Junctions to the local repository checkout.
    Edits to SKILL.md in the repo immediately reflect across all agent environments.
  - "copy": Copies the skill directory directly (useful for standalone pip installs).
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from wcu.paths import repo_root


def get_target_dirs() -> Dict[str, Path]:
    """Returns mapping of host names to their respective skill directories."""
    home = Path.home()
    return {
        "gemini": home / ".gemini" / "config" / "skills" / "windows-computer-use",
        "agents": home / ".agents" / "skills" / "windows-computer-use",
        "claude": home / ".claude" / "skills" / "windows-computer-use",
    }


def find_canonical_skill_dir() -> Optional[Path]:
    """Finds the canonical skill directory in the source repository if available."""
    # From repo_root() (windows-computer-use), check parent workspace
    try:
        candidate = repo_root().parent / "skills" / "windows-computer-use"
        if candidate.is_dir() and (candidate / "SKILL.md").is_file():
            return candidate.resolve()
    except Exception:
        pass

    # Check cwd parents as fallback
    cur = Path.cwd().resolve()
    for p in [cur] + list(cur.parents):
        candidate = p / "skills" / "windows-computer-use"
        if candidate.is_dir() and (candidate / "SKILL.md").is_file():
            return candidate.resolve()

    return None


def find_packaged_skill_dir() -> Path:
    """Returns the bundled skill directory shipped inside the wcu package."""
    return Path(__file__).resolve().parent


def is_reparse_point(path: Path) -> bool:
    """Returns True if path is an NTFS junction or symlink."""
    try:
        os.readlink(str(path))
        return True
    except (OSError, ValueError, AttributeError):
        return False


def resolve_link_target(path: Path) -> Optional[Path]:
    """Resolves link target for a junction or symlink, stripping Win32 prefixes."""
    try:
        raw = os.readlink(str(path))
        if raw.startswith("\\\\?\\"):
            raw = raw[4:]
        return Path(raw).resolve()
    except (OSError, ValueError, AttributeError):
        return None


def create_directory_junction(source: Path, target: Path) -> None:
    """Creates an NTFS Directory Junction from target -> source."""
    target_path = Path(target)
    source_path = Path(source).resolve()

    target_path.parent.mkdir(parents=True, exist_ok=True)

    if is_reparse_point(target_path):
        os.rmdir(str(target_path))
    elif target_path.is_dir():
        shutil.rmtree(str(target_path))
    elif target_path.exists():
        target_path.unlink()

    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(source_path), str(target_path))
    else:
        os.symlink(str(source_path), str(target_path), target_is_directory=True)


def copy_skill_dir(source: Path, target: Path) -> None:
    """Copies all files from source directory into target directory."""
    target_path = Path(target)
    source_path = Path(source).resolve()

    if is_reparse_point(target_path):
        os.rmdir(str(target_path))

    target_path.mkdir(parents=True, exist_ok=True)

    for item in source_path.iterdir():
        if item.name.startswith((".", "__pycache__")):
            continue
        dest = target_path / item.name
        if item.is_dir():
            if dest.exists():
                shutil.rmtree(str(dest))
            shutil.copytree(str(item), str(dest))
        elif item.is_file():
            shutil.copy2(str(item), str(dest))


def inspect_skills() -> Dict[str, Any]:
    """Inspects the state of WCU skills across all supported agent environments."""
    canonical = find_canonical_skill_dir()
    packaged = find_packaged_skill_dir()
    targets = get_target_dirs()

    report: Dict[str, Any] = {
        "canonical_source": str(canonical) if canonical else None,
        "packaged_source": str(packaged),
        "targets": {},
    }

    for name, path in targets.items():
        exists = path.exists()
        reparse = is_reparse_point(path)
        link_target = resolve_link_target(path) if reparse else None

        skill_md = path / "SKILL.md" if exists else None
        has_skill_md = skill_md.is_file() if skill_md else False

        points_to_canonical = False
        if link_target and canonical:
            try:
                points_to_canonical = link_target.resolve() == canonical.resolve()
            except Exception:
                pass

        kind = "junction" if reparse else ("directory" if exists else "missing")
        report["targets"][name] = {
            "path": str(path),
            "exists": exists,
            "kind": kind,
            "link_target": str(link_target) if link_target else None,
            "points_to_canonical": points_to_canonical,
            "has_skill_md": has_skill_md,
        }

    return report


def install_skills(
    mode: str = "link",
    targets: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Installs or links WCU skills into agent environments.

    Args:
        mode: "link" (directory junction to repo) or "copy" (file mirroring).
        targets: List of target keys ('gemini', 'agents', 'claude'), or None for all.

    Returns:
        Summary dict of performed actions.
    """
    canonical = find_canonical_skill_dir()
    packaged = find_packaged_skill_dir()
    all_targets = get_target_dirs()

    active_targets = targets if targets else list(all_targets.keys())

    # Fallback to copy if link mode requested without source repository
    actual_mode = mode
    if actual_mode == "link" and not canonical:
        actual_mode = "copy"

    source = canonical if canonical else packaged

    results: Dict[str, Any] = {
        "requested_mode": mode,
        "effective_mode": actual_mode,
        "source": str(source),
        "actions": {},
    }

    agents_path = all_targets["agents"]

    for name in active_targets:
        if name not in all_targets:
            results["actions"][name] = {"status": "error", "error": f"Unknown target: {name}"}
            continue

        target_path = all_targets[name]
        try:
            if actual_mode == "link":
                # For claude, point to agents junction if claude is configured to link to agents
                if name == "claude" and "agents" in active_targets:
                    create_directory_junction(agents_path, target_path)
                    results["actions"][name] = {
                        "status": "linked",
                        "target": str(target_path),
                        "destination": str(agents_path),
                    }
                else:
                    create_directory_junction(source, target_path)
                    results["actions"][name] = {
                        "status": "linked",
                        "target": str(target_path),
                        "destination": str(source),
                    }
            else:
                copy_skill_dir(source, target_path)
                results["actions"][name] = {
                    "status": "copied",
                    "target": str(target_path),
                    "source": str(source),
                }
        except Exception as e:
            results["actions"][name] = {
                "status": "error",
                "error": str(e),
                "target": str(target_path),
            }

    return results
