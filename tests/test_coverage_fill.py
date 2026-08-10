"""Coverage fill for resolve, wiki, backup, redaction, facts_io, bugreport, retrieval.

Targets specific uncovered branches in modules that are at 88-96% coverage.
"""
from __future__ import annotations

import json
import logging
import subprocess
from datetime import date
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from lorekeep.compile.resolve import _richer_summary, _merge_descriptions
from lorekeep.facts_io import read_facts
from lorekeep.models import Node, Edge
from lorekeep.redaction import redact_text


# ======================================================================
# resolve.py — pure helper functions
# ======================================================================

def test_richer_summary_left_empty():
    """When left is empty/None, returns normalized right."""
    assert _richer_summary("", "hello") == "hello"
    assert _richer_summary(None, "world") == "world"


def test_richer_summary_right_empty():
    """When right is empty/None, returns normalized left."""
    assert _richer_summary("hello", "") == "hello"
    assert _richer_summary("world", None) == "world"


def test_richer_summary_both_valid_picks_richer():
    """When both are valid strings, picks the one with more unique words."""
    result = _richer_summary("a", "alpha beta gamma")
    assert "alpha" in result


def test_merge_descriptions_left_empty():
    assert _merge_descriptions("", "hello") == "hello"
    assert _merge_descriptions(None, "world") == "world"


def test_merge_descriptions_right_empty():
    assert _merge_descriptions("hello", "") == "hello"
    assert _merge_descriptions("world", None) == "world"


def test_merge_descriptions_dedups_contained_paragraph():
    """A paragraph contained within an existing one replaces it."""
    left = "This is a very long paragraph with lots of detail"
    right = "long paragraph"
    result = _merge_descriptions(left, right)
    # right is contained in left, so left's paragraph should be kept (replaced position)
    assert "lots of detail" in result


# ======================================================================
# redaction.py — Path.home() exception
# ======================================================================

def test_redact_text_home_resolution_fails(monkeypatch):
    """When Path.home() raises, redaction still works without home filtering."""
    monkeypatch.setattr(Path, "home", lambda: (_ for _ in ()).throw(RuntimeError("no home")))
    result = redact_text("some text with api_key=secret123", home=None)
    assert "secret123" not in result
    assert "[REDACTED]" in result


# ======================================================================
# facts_io.py — blank line skip
# ======================================================================

def test_read_facts_skips_blank_lines(tmp_path: Path):
    """Blank lines in facts.jsonl are silently skipped."""
    node = Node(id="svc:x", type="service", ns=("ns",), props={})
    f = tmp_path / "facts.jsonl"
    f.write_text(
        json.dumps(node.model_dump(mode="json", by_alias=True)) + "\n"
        "\n"
        "  \n"
    )
    facts = read_facts(f)
    assert len(facts) == 1


# ======================================================================
# backup.py — git error handling
# ======================================================================

def test_backup_has_remote_catches_error(tmp_path: Path, monkeypatch):
    """has_remote returns False when git fails."""
    from lorekeep.backup import has_remote, BackupError
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".git").mkdir()

    def fail_git(args, cwd):
        raise BackupError("git not found")

    monkeypatch.setattr("lorekeep.backup._git", fail_git)
    assert has_remote(fake_home) is False


def test_backup_remote_sha_detached_head(tmp_path: Path, monkeypatch):
    """_remote_sha returns None when branch is HEAD (detached)."""
    from lorekeep.backup import _remote_sha
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr("lorekeep.backup._git", lambda a, c: "HEAD")
    assert _remote_sha(fake_home) is None


def test_backup_reconcile_propagates_fetch_error(tmp_path: Path, monkeypatch):
    """Manual callers see fetch failures; daemon sync catches them upstream."""
    from lorekeep.backup import _reconcile_remote, BackupError
    fake_home = tmp_path / "home"
    fake_home.mkdir()

    def fail_git(args, cwd):
        if "fetch" in args:
            raise BackupError("fetch failed")
        return "main"

    monkeypatch.setattr("lorekeep.backup._git", fail_git)
    with pytest.raises(BackupError, match="fetch failed"):
        _reconcile_remote(fake_home)


# ======================================================================
# bugreport.py — _gh_cli_token, emit exception, skip events
# ======================================================================

def test_gh_cli_token_no_gh(monkeypatch):
    """Returns empty string when gh is not installed."""
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    from lorekeep.bugreport import _gh_cli_token
    assert _gh_cli_token() == ""


def test_gh_cli_token_success(monkeypatch):
    """Returns token when gh auth token succeeds."""
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "ghp_123456\n"
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/gh" if cmd == "gh" else None)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: mock_proc)
    from lorekeep.bugreport import _gh_cli_token
    assert _gh_cli_token() == "ghp_123456"


def test_gh_cli_token_failure(monkeypatch):
    """Returns empty string when gh auth token fails."""
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stdout = ""
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/gh" if cmd == "gh" else None)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: mock_proc)
    from lorekeep.bugreport import _gh_cli_token
    assert _gh_cli_token() == ""


def test_gh_cli_token_oserror(monkeypatch):
    """Returns empty string when subprocess raises OSError."""
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/gh" if cmd == "gh" else None)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    from lorekeep.bugreport import _gh_cli_token
    assert _gh_cli_token() == ""


def test_bugreport_handler_emit_swallows_exception(monkeypatch):
    """BugReportHandler.emit never propagates exceptions."""
    from lorekeep.bugreport import BugReportHandler
    handler = BugReportHandler()
    # Force _handle to raise
    monkeypatch.setattr(handler, "_handle", lambda record: (_ for _ in ()).throw(RuntimeError("boom")))
    record = logging.LogRecord(
        name="test", level=logging.ERROR, pathname="", lineno=0,
        msg="test error", args=(), exc_info=None,
    )
    handler.emit(record)  # should not raise


def test_bugreport_handler_skips_skip_events():
    """Records with events in _SKIP_EVENTS are silently ignored."""
    from lorekeep.bugreport import BugReportHandler, _SKIP_EVENTS
    handler = BugReportHandler()
    skip_event = next(iter(_SKIP_EVENTS))
    record = logging.LogRecord(
        name="test", level=logging.ERROR, pathname="", lineno=0,
        msg="skipped event", args=(), exc_info=None,
    )
    record.__dict__["event"] = skip_event
    # Should return without appending — no crash, no error
    handler._handle(record)


# ======================================================================
# wiki.py — edge cases in helper functions
# ======================================================================

def test_yaml_scalar_none():
    from lorekeep.wiki import _yaml_scalar
    assert _yaml_scalar(None) == "null"


def test_yaml_list_empty():
    from lorekeep.wiki import _yaml_list
    assert _yaml_list([]) == "[]"


def test_fmt_validity_until():
    """valid_to without valid_from shows 'until <date>'."""
    from lorekeep.wiki import _fmt_validity
    result = _fmt_validity(None, date(2026, 6, 1))
    assert "until" in result
    assert "2026-06-01" in result


def test_fmt_validity_present():
    """valid_from without valid_to shows '<date> → present'."""
    from lorekeep.wiki import _fmt_validity
    result = _fmt_validity(date(2026, 1, 1), None)
    assert "present" in result


def test_truncate_summary_long():
    """Long summaries are truncated with ellipsis."""
    from lorekeep.wiki import _summary_text
    node = Node(id="svc:x", type="service", ns=("ns",),
                props={"summary": "x" * 300})
    result = _summary_text(node, limit=50)
    assert len(result) <= 50
    assert result.endswith("…")


# ======================================================================
# eval/retrieval.py — check failures + temporal
# ======================================================================

def test_retrieval_check_failure():
    """A question with no matching nodes/edges fails the check."""
    from lorekeep.eval.retrieval import _check
    from lorekeep.perm.ns import ScopedGraph
    from lorekeep.store.graph import GraphStore

    node = Node(id="svc:a", type="service", ns=("public",), props={})
    store = GraphStore([node], [])
    scoped = ScopedGraph(store, allowed_ns={"public"})

    # multihop question where the expected node doesn't exist
    assert not _check(scoped, {
        "kind": "multihop", "start": "svc:a", "depth": 1,
        "expect_node_ids": ["svc:nonexistent"],
    })


def test_retrieval_check_unknown_kind():
    """Unknown kind returns False."""
    from lorekeep.eval.retrieval import _check
    from lorekeep.perm.ns import ScopedGraph
    from lorekeep.store.graph import GraphStore

    store = GraphStore([], [])
    scoped = ScopedGraph(store, allowed_ns={"public"})
    assert not _check(scoped, {"kind": "bogus"})
