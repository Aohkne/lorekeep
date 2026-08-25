"""Claude Code's project directory name must be reproduced exactly.

A slug that differs by one character resolves to a directory that does not
exist, and session import then reports "no session found" instead of failing
loudly — so these cases are worth pinning.
"""
from __future__ import annotations

from pathlib import Path

from lorekeep.importer.claude import _project_slug, find_current_session


def test_slashes_become_dashes():
    assert _project_slug(Path("/Users/a/Desktop/proj")) == "-Users-a-Desktop-proj"


def test_underscores_become_dashes():
    """Claude Code folds '_' into '-'; a literal underscore never appears."""
    slug = _project_slug(Path("/Users/a_b/NenTangAI_RAG/work"))
    assert slug == "-Users-a-b-NenTangAI-RAG-work"
    assert "_" not in slug


def test_mixed_separators_in_one_path():
    slug = _project_slug(Path("/Users/x_y/a-b/c_d-e"))
    assert slug == "-Users-x-y-a-b-c-d-e"


def test_missing_project_dir_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    assert find_current_session(Path("/Users/nobody/nothing_here")) is None


def test_finds_dir_named_with_folded_underscores(tmp_path, monkeypatch):
    """The regression: an underscored path must locate its transcript dir."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    cwd = Path("/Users/a_b/My_Project")
    project_dir = tmp_path / ".claude" / "projects" / "-Users-a-b-My-Project"
    project_dir.mkdir(parents=True)
    (project_dir / "session.jsonl").write_text("{}\n", encoding="utf-8")

    assert find_current_session(cwd) == project_dir


def test_dir_without_transcripts_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    cwd = Path("/Users/a_b/My_Project")
    (tmp_path / ".claude" / "projects" / "-Users-a-b-My-Project").mkdir(parents=True)

    assert find_current_session(cwd) is None
