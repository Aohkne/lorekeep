"""Tests for journal.py edge cases: error paths, status updates, atomic writes."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from lorekeep.journal import load_journals, update_journal_status
from lorekeep.models import JournalEntry


def _make_entry(status: str = "pending", proposed_at: str = "2026-01-01T00:00:00Z") -> dict:
    return {
        "agent": "test", "ns": "backend", "confidence": 1.0,
        "proposed_at": proposed_at, "status": status,
        "fact": {"kind": "node", "id": "svc:x", "type": "service",
                 "ns": ["backend"], "props": {}, "src": []},
    }


# ---------------------------------------------------------------------------
# load_journals
# ---------------------------------------------------------------------------

class TestLoadJournals:
    def test_missing_dir_returns_empty(self, tmp_path: Path):
        assert load_journals(tmp_path / "nonexistent") == []

    def test_unreadable_file_skipped(self, tmp_path: Path, monkeypatch):
        pending = tmp_path / "pending"
        ns = pending / "backend"
        ns.mkdir(parents=True)
        (ns / "journal.jsonl").write_text(json.dumps(_make_entry()) + "\n")

        # Make read_text fail
        original_read = Path.read_text
        call_count = [0]

        def flaky_read(self, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise OSError("permission denied")
            return original_read(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", flaky_read)
        result = load_journals(pending)
        assert result == []

    def test_invalid_json_line_skipped(self, tmp_path: Path):
        pending = tmp_path / "pending"
        ns = pending / "backend"
        ns.mkdir(parents=True)
        (ns / "journal.jsonl").write_text(
            json.dumps(_make_entry()) + "\n"
            "NOT VALID JSON\n"
        )
        result = load_journals(pending)
        assert len(result) == 1

    def test_empty_lines_skipped(self, tmp_path: Path):
        pending = tmp_path / "pending"
        ns = pending / "backend"
        ns.mkdir(parents=True)
        (ns / "journal.jsonl").write_text(
            "\n\n" + json.dumps(_make_entry()) + "\n\n"
        )
        result = load_journals(pending)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# update_journal_status
# ---------------------------------------------------------------------------

class TestUpdateJournalStatus:
    def test_pending_entry_updated_to_merged(self, tmp_path: Path):
        pending = tmp_path / "pending"
        ns = pending / "backend"
        ns.mkdir(parents=True)
        entry = _make_entry(status="pending", proposed_at="2026-01-01T00:00:00Z")
        (ns / "journal.jsonl").write_text(json.dumps(entry, sort_keys=True) + "\n")

        update_journal_status(pending, "backend", {"2026-01-01T00:00:00Z"}, "merged")

        updated = json.loads((ns / "journal.jsonl").read_text().strip())
        assert updated["status"] == "merged"

    def test_non_pending_entry_not_updated(self, tmp_path: Path):
        pending = tmp_path / "pending"
        ns = pending / "backend"
        ns.mkdir(parents=True)
        entry = _make_entry(status="merged")
        (ns / "journal.jsonl").write_text(json.dumps(entry, sort_keys=True) + "\n")

        update_journal_status(pending, "backend", {"2026-01-01T00:00:00Z"}, "flagged")

        updated = json.loads((ns / "journal.jsonl").read_text().strip())
        assert updated["status"] == "merged"  # unchanged

    def test_blank_line_preserved(self, tmp_path: Path):
        pending = tmp_path / "pending"
        ns = pending / "backend"
        ns.mkdir(parents=True)
        entry = _make_entry(status="pending")
        content = json.dumps(entry, sort_keys=True) + "\n\n"
        (ns / "journal.jsonl").write_text(content)

        update_journal_status(pending, "backend", {"2026-01-01T00:00:00Z"}, "merged")

        result = (ns / "journal.jsonl").read_text()
        assert result.count("\n") >= 2  # blank line preserved

    def test_corrupt_json_line_preserved(self, tmp_path: Path):
        pending = tmp_path / "pending"
        ns = pending / "backend"
        ns.mkdir(parents=True)
        entry = _make_entry(status="pending")
        content = json.dumps(entry, sort_keys=True) + "\nCORRUPT\n"
        (ns / "journal.jsonl").write_text(content)

        update_journal_status(pending, "backend", {"2026-01-01T00:00:00Z"}, "merged")

        result_lines = (ns / "journal.jsonl").read_text().splitlines()
        assert "CORRUPT" in result_lines

    def test_no_file_is_noop(self, tmp_path: Path):
        pending = tmp_path / "pending"
        pending.mkdir()
        update_journal_status(pending, "backend", {"x"}, "merged")
        # No crash, no file created
        assert not (pending / "backend" / "journal.jsonl").exists()

    def test_replace_failure_cleans_up_temp(self, tmp_path: Path, monkeypatch):
        """When os.replace fails, temp file is cleaned up and exception propagates."""
        pending = tmp_path / "pending"
        ns = pending / "backend"
        ns.mkdir(parents=True)
        entry = _make_entry(status="pending")
        (ns / "journal.jsonl").write_text(json.dumps(entry, sort_keys=True) + "\n")

        original_replace = os.replace

        def fail_replace(src, dst):
            raise OSError("disk full")

        monkeypatch.setattr(os, "replace", fail_replace)

        with pytest.raises(OSError, match="disk full"):
            update_journal_status(pending, "backend",
                                  {"2026-01-01T00:00:00Z"}, "merged")

        # temp files cleaned up
        leftover = list(ns.glob("journal.jsonl.*.tmp"))
        assert leftover == []
        # original file untouched
        assert "pending" in (ns / "journal.jsonl").read_text()
