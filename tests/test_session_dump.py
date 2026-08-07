"""Tests for the zero-LLM session transcript dump.

The invariants here are economic, not cosmetic: extract caches per chunk on
``sha256(path ‖ text)``, so any churn in a filename or in a file's bytes is a
repeated LLM bill on every daemon cycle.
"""
import inspect
import json
import os

import pytest

from lorekeep.importer import session_dump
from lorekeep.importer.claude import ConversationTurn
from lorekeep.importer.session_dump import (
    TRUNCATION_MARKER,
    dump_session_turns,
    prune_sessions,
    safe_key,
    turns_digest,
)


def _turns(n: int, *, size: int = 100, offset: int = 0) -> list[ConversationTurn]:
    return [
        ConversationTurn(
            user_content=f"q{i} " + "u" * size,
            assistant_text=f"a{i} " + "s" * size,
            tool_calls=["read_file"],
        )
        for i in range(offset, offset + n)
    ]


# ── layout ────────────────────────────────────────────────────────────────


def test_dump_writes_numbered_batches(tmp_path):
    written = dump_session_turns(
        _turns(10), tmp_path, namespace="cursor-session",
        session_key="abc123", max_chars=900,
    )
    assert written
    names = sorted(p.name for p in written)
    assert names == [f"abc123-{i:03d}.md" for i in range(1, len(written) + 1)]
    assert all(p.parent.name == "cursor-session" for p in written)


def test_frontmatter_records_global_position(tmp_path):
    written = dump_session_turns(
        _turns(10), tmp_path, namespace="cursor-session",
        session_key="abc123", max_chars=900,
    )
    body = written[1].read_text()
    assert "source: cursor-session" in body
    assert "session: abc123" in body
    assert "batch: 002" in body


def test_turn_numbers_are_batch_local(tmp_path):
    """A batch's bytes must not depend on how many batches precede it."""
    written = dump_session_turns(
        _turns(10), tmp_path, namespace="cursor-session",
        session_key="abc123", max_chars=900,
    )
    assert len(written) > 1
    for path in written:
        assert "## Turn 1" in path.read_text()


def test_session_key_is_sanitized(tmp_path):
    written = dump_session_turns(
        _turns(1), tmp_path, namespace="cursor-session",
        session_key="rollout-2026/01/02T03:04",
    )
    assert written[0].name == "rollout-2026-01-02T03-04-001.md"


def test_safe_key_never_returns_empty():
    assert safe_key("///") == "unknown"


def test_empty_turns_write_nothing(tmp_path):
    assert dump_session_turns([], tmp_path, namespace="cursor-session", session_key="x") == []
    assert not (tmp_path / "cursor-session").exists()


# ── partitioning: overlap=0 ───────────────────────────────────────────────


def test_batches_partition_the_conversation(tmp_path):
    """overlap=0 means no turn appears twice — a repeated turn is a repeated bill."""
    written = dump_session_turns(
        _turns(12), tmp_path, namespace="cursor-session",
        session_key="abc", max_chars=900,
    )
    assert len(written) > 1
    seen: list[str] = []
    for path in written:
        for line in path.read_text().splitlines():
            if line.startswith("**User:** q"):
                seen.append(line)
    assert len(seen) == len(set(seen)), "a turn was dumped into two batches"
    assert len(seen) == 12


# ── determinism and cache friendliness ────────────────────────────────────


def test_redump_is_byte_identical_and_skips_writes(tmp_path):
    turns = _turns(8)
    first = dump_session_turns(
        turns, tmp_path, namespace="cursor-session", session_key="abc", max_chars=900,
    )
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in first}

    again = dump_session_turns(
        turns, tmp_path, namespace="cursor-session", session_key="abc", max_chars=900,
    )
    assert again == [], "unchanged conversation must not be re-rendered"
    for path, (content, mtime) in before.items():
        assert path.read_bytes() == content
        assert path.stat().st_mtime_ns == mtime


def test_lost_manifest_still_does_not_touch_mtime(tmp_path):
    """The per-file content guard has to hold on its own."""
    turns = _turns(8)
    first = dump_session_turns(
        turns, tmp_path, namespace="cursor-session", session_key="abc", max_chars=900,
    )
    (tmp_path / "cursor-session" / ".import-manifest.json").unlink()
    before = {p: p.stat().st_mtime_ns for p in first}

    assert dump_session_turns(
        turns, tmp_path, namespace="cursor-session", session_key="abc", max_chars=900,
    ) == []
    for path, mtime in before.items():
        assert path.stat().st_mtime_ns == mtime


def test_appending_turns_only_changes_the_last_file(tmp_path):
    """The daemon re-reads a growing prefix; earlier chunks must stay cached."""
    turns = _turns(12)
    first = dump_session_turns(
        turns, tmp_path, namespace="cursor-session", session_key="abc", max_chars=900,
    )
    assert len(first) >= 3
    frozen = {p: p.read_bytes() for p in first[:-1]}

    grown = turns + _turns(4, offset=12)
    written = dump_session_turns(
        grown, tmp_path, namespace="cursor-session", session_key="abc", max_chars=900,
    )
    for path, content in frozen.items():
        assert path.read_bytes() == content, f"{path.name} was rewritten"
    assert written, "growing a session must produce new content"
    assert all(p not in frozen for p in written)


def test_tool_calls_are_order_insensitive(tmp_path):
    a = [ConversationTurn("q", "a", ["read_file", "edit_file"])]
    b = [ConversationTurn("q", "a", ["edit_file", "read_file"])]
    dump_session_turns(a, tmp_path, namespace="cursor-session", session_key="k")
    body = (tmp_path / "cursor-session" / "k-001.md").read_text()

    other = tmp_path / "other"
    dump_session_turns(b, other, namespace="cursor-session", session_key="k")
    assert (other / "cursor-session" / "k-001.md").read_text() == body
    assert "**Tools:** edit_file, read_file" in body


def test_digest_reflects_content_not_identity():
    assert turns_digest(_turns(3)) == turns_digest(_turns(3))
    assert turns_digest(_turns(3)) != turns_digest(_turns(4))


# ── caps ──────────────────────────────────────────────────────────────────


def test_max_batches_caps_the_prefix(tmp_path):
    written = dump_session_turns(
        _turns(40), tmp_path, namespace="cursor-session",
        session_key="abc", max_chars=300, max_batches=3,
    )
    assert len(written) == 3
    assert sorted(p.name for p in written) == ["abc-001.md", "abc-002.md", "abc-003.md"]


def test_long_turns_are_truncated_with_a_marker(tmp_path):
    turns = [ConversationTurn("q " + "u" * 5_000, "a " + "s" * 5_000, [])]
    written = dump_session_turns(
        turns, tmp_path, namespace="cursor-session",
        session_key="abc", max_turn_chars=100,
    )
    body = written[0].read_text()
    assert body.count(TRUNCATION_MARKER.strip()) == 2
    assert "u" * 200 not in body


def test_dry_run_writes_nothing(tmp_path):
    written = dump_session_turns(
        _turns(6), tmp_path, namespace="cursor-session",
        session_key="abc", max_chars=400, dry_run=True,
    )
    assert written
    assert not (tmp_path / "cursor-session").exists()


# ── determinism guard ─────────────────────────────────────────────────────


def test_module_imports_no_clock():
    """A timestamp in the markdown would make every recompile a cache miss."""
    src = inspect.getsource(session_dump)
    assert "import time" not in src
    assert "datetime" not in src


# ── prune ─────────────────────────────────────────────────────────────────


def test_prune_keeps_the_newest_sessions(tmp_path):
    for i, key in enumerate(["s1", "s2", "s3", "s4"]):
        dump_session_turns(
            _turns(2), tmp_path, namespace="cursor-session",
            session_key=key, max_chars=10_000,
        )
        for p in (tmp_path / "cursor-session").glob(f"{key}-*.md"):
            os.utime(p, (1_000 + i, 1_000 + i))

    removed = prune_sessions(tmp_path, "cursor-session", retain=2)
    remaining = {p.name for p in (tmp_path / "cursor-session").glob("*.md")}
    assert remaining == {"s3-001.md", "s4-001.md"}
    assert {p.name for p in removed} == {"s1-001.md", "s2-001.md"}


def test_prune_drops_manifest_entries(tmp_path):
    for key in ("s1", "s2"):
        dump_session_turns(
            _turns(2), tmp_path, namespace="cursor-session", session_key=key,
        )
    for p in (tmp_path / "cursor-session").glob("s1-*.md"):
        os.utime(p, (1_000, 1_000))

    prune_sessions(tmp_path, "cursor-session", retain=1)
    manifest = json.loads(
        (tmp_path / "cursor-session" / ".import-manifest.json").read_text()
    )
    assert "cursor-session:s1" not in manifest
    assert "cursor-session:s2" in manifest


def test_prune_is_a_noop_under_the_limit(tmp_path):
    dump_session_turns(_turns(2), tmp_path, namespace="cursor-session", session_key="s1")
    assert prune_sessions(tmp_path, "cursor-session", retain=5) == []
    assert (tmp_path / "cursor-session" / "s1-001.md").exists()


def test_prune_refuses_agent_authored_namespaces(tmp_path):
    """Memory files are written by the agent — deleting them loses them for good."""
    with pytest.raises(ValueError, match="non-session namespace"):
        prune_sessions(tmp_path, "claude-memory", retain=1)


def test_prune_on_missing_dir_is_harmless(tmp_path):
    assert prune_sessions(tmp_path, "cursor-session", retain=2) == []


def test_stale_batches_cleaned_on_shrink(tmp_path):
    """When batch count shrinks, old batch files are removed."""
    # Simulate a prior 6-batch dump by writing stale files
    dest_dir = tmp_path / "claude-session"
    dest_dir.mkdir(parents=True)
    for i in range(1, 7):
        (dest_dir / f"old-session-{i:03d}.md").write_text(f"# Old batch {i}")

    # New dump produces only 1 batch
    turns = _turns(1)
    dump_session_turns(
        turns, tmp_path, namespace="claude-session",
        session_key="old-session", max_chars=10000,
    )

    # Stale files 002-006 should be cleaned up
    remaining = sorted(dest_dir.glob("old-session-*.md"))
    assert len(remaining) == 1  # only the new batch 001
    assert remaining[0].name == "old-session-001.md"
