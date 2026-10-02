"""Unit tests for WCU installer and skill release automation."""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from wcu import installer
from wcu.cli import main as cli_main


def test_find_dirs():
    canonical = installer.find_canonical_skill_dir()
    packaged = installer.find_packaged_skill_dir()
    assert canonical is not None
    assert (canonical / "SKILL.md").is_file()
    assert packaged.is_dir()
    assert (packaged / "SKILL.md").is_file()


def test_get_target_dirs():
    targets = installer.get_target_dirs()
    assert "gemini" in targets
    assert "agents" in targets
    assert "claude" in targets
    for name, p in targets.items():
        assert isinstance(p, Path)
        assert p.name == "windows-computer-use"


def test_reparse_point_and_junction(tmp_path):
    src = tmp_path / "source"
    src.mkdir()
    (src / "test.txt").write_text("hello world", encoding="utf-8")

    dst = tmp_path / "target_junction"

    # Create junction
    installer.create_directory_junction(src, dst)

    assert dst.exists()
    assert (dst / "test.txt").read_text(encoding="utf-8") == "hello world"

    # Reparse check
    assert installer.is_reparse_point(dst)
    target = installer.resolve_link_target(dst)
    assert target is not None
    assert target.resolve() == src.resolve()

    # Re-creating junction on existing destination should succeed cleanly
    installer.create_directory_junction(src, dst)
    assert dst.exists()
    assert installer.is_reparse_point(dst)

    # Clean up junction without touching source
    os.rmdir(str(dst))
    assert not dst.exists()
    assert src.exists()
    assert (src / "test.txt").is_file()


def test_copy_skill_dir(tmp_path):
    src = tmp_path / "source"
    src.mkdir()
    (src / "SKILL.md").write_text("# Skill", encoding="utf-8")
    sub = src / "sub"
    sub.mkdir()
    (sub / "nested.txt").write_text("nested", encoding="utf-8")

    dst = tmp_path / "dest_copy"
    installer.copy_skill_dir(src, dst)

    assert dst.is_dir()
    assert (dst / "SKILL.md").read_text(encoding="utf-8") == "# Skill"
    assert (dst / "sub" / "nested.txt").read_text(encoding="utf-8") == "nested"


def test_install_skills_mocked(tmp_path, monkeypatch):
    gemini_dir = tmp_path / "gemini" / "windows-computer-use"
    agents_dir = tmp_path / "agents" / "windows-computer-use"
    claude_dir = tmp_path / "claude" / "windows-computer-use"

    mock_targets = {
        "gemini": gemini_dir,
        "agents": agents_dir,
        "claude": claude_dir,
    }
    monkeypatch.setattr(installer, "get_target_dirs", lambda: mock_targets)

    # Link mode
    res = installer.install_skills(mode="link")
    assert res["requested_mode"] == "link"
    assert res["actions"]["gemini"]["status"] == "linked"
    assert res["actions"]["agents"]["status"] == "linked"
    assert res["actions"]["claude"]["status"] == "linked"

    assert gemini_dir.exists()
    assert agents_dir.exists()
    assert claude_dir.exists()
    assert (gemini_dir / "SKILL.md").is_file()

    # Copy mode overwrite
    res_copy = installer.install_skills(mode="copy")
    assert res_copy["requested_mode"] == "copy"
    assert res_copy["actions"]["gemini"]["status"] == "copied"
    assert (gemini_dir / "SKILL.md").is_file()


def test_cli_install_skill_check(capsys):
    ret = cli_main(["install-skill", "--check"])
    assert ret == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["ok"] is True
    assert data["status"] == "ok"
    assert "targets" in data["result"]
