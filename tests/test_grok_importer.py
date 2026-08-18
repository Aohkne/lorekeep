"""Tests for the Grok Build session importer.

Grok Build stores sessions as directories with ``chat_history.jsonl`` (JSONL
conversation log). This test suite verifies the parse layer converts those
records into ``ConversationTurn`` objects correctly, and that the zero-LLM
dump path produces markdown batches under ``raw/grok-session/``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lorekeep.importer import grok
from lorekeep.importer.claude import ConversationTurn


FIXTURE = Path(__file__).parent / "fixtures" / "grok" / "chat_history.jsonl"


# ---------------------------------------------------------------------------
# parse_transcript
# ---------------------------------------------------------------------------

class TestParseTranscript:
    def test_extracts_user_assistant_pairs(self, tmp_path):
        turns = grok.parse_transcript(FIXTURE.parent)
        assert len(turns) == 2
        assert "architecture" in turns[0].user_content.lower()
        assert "7 tools" in turns[0].assistant_text or "FastMCP" in turns[0].assistant_text
        assert "journal" in turns[1].user_content.lower()

    def test_skips_system_and_reasoning(self, tmp_path):
        turns = grok.parse_transcript(FIXTURE.parent)
        # system prompt and reasoning must not appear in any turn
        for t in turns:
            assert "You are a Grok Build agent" not in t.user_content
            assert "Let me read the file first" not in t.assistant_text

    def test_collects_tool_calls(self, tmp_path):
        turns = grok.parse_transcript(FIXTURE.parent)
        assert "read_file" in turns[0].tool_calls
        assert "search" in turns[1].tool_calls

    def test_empty_directory_returns_empty(self, tmp_path):
        assert grok.parse_transcript(tmp_path) == []

    def test_handles_string_content(self, tmp_path):
        """Grok sometimes sends content as a plain string, not a list."""
        d = tmp_path / "session-abc"
        d.mkdir()
        (d / "chat_history.jsonl").write_text(
            json.dumps({"type": "user", "content": "Hello"})
            + "\n"
            + json.dumps({"type": "assistant", "content": "Hi there", "model_id": "test"})
            + "\n"
        )
        turns = grok.parse_transcript(d)
        assert len(turns) == 1
        assert turns[0].user_content == "Hello"
        assert turns[0].assistant_text == "Hi there"


# ---------------------------------------------------------------------------
# session_key
# ---------------------------------------------------------------------------

class TestSessionKey:
    def test_key_is_stable(self):
        session_dir = Path("/fake/%2FUsers%2Fdev%2Fproject/abc12345-6789-0123-abcd-ef01234567ab")
        key = grok.session_key(session_dir)
        assert key == "project-abc12345"

    def test_key_decodes_url_encoded_cwd(self):
        session_dir = Path("/fake/%2FUsers%2Fmanhpt1%2FWorkspace%2Florekeep/019fe088-0cea-7ce0-9e4f-f4d1efc14487")
        key = grok.session_key(session_dir)
        assert "lorekeep" in key
        assert "019fe088" in key


# ---------------------------------------------------------------------------
# locate_session
# ---------------------------------------------------------------------------

class TestLocateSession:
    def test_finds_session_by_cwd(self, tmp_path, monkeypatch):
        cwd = tmp_path / "myproject"
        cwd.mkdir()
        grok_home = tmp_path / "grok-home"
        encoded = urllib_encode(str(cwd.resolve()))
        session_dir = grok_home / "sessions" / encoded / "abc12345-dead-beef"
        session_dir.mkdir(parents=True)
        (session_dir / "chat_history.jsonl").write_text(
            json.dumps({"type": "user", "content": "test"})
            + "\n"
            + json.dumps({"type": "assistant", "content": "ok"})
            + "\n"
        )
        (session_dir / "summary.json").write_text('{"id": "abc12345"}')

        monkeypatch.setenv("GROK_HOME", str(grok_home))
        result = grok.locate_session(cwd)
        assert result is not None
        assert result.name == "abc12345-dead-beef"

    def test_picks_newest_when_multiple(self, tmp_path, monkeypatch):
        import time
        cwd = tmp_path / "myproject"
        cwd.mkdir()
        grok_home = tmp_path / "grok-home"
        encoded = urllib_encode(str(cwd.resolve()))
        base = grok_home / "sessions" / encoded

        old_session = base / "old-uuid"
        old_session.mkdir(parents=True)
        (old_session / "chat_history.jsonl").write_text("{}\n")
        (old_session / "summary.json").write_text("{}")

        time.sleep(0.05)

        new_session = base / "new-uuid"
        new_session.mkdir(parents=True)
        (new_session / "chat_history.jsonl").write_text("{}\n")
        (new_session / "summary.json").write_text("{}")

        monkeypatch.setenv("GROK_HOME", str(grok_home))
        result = grok.locate_session(cwd)
        assert result is not None
        assert result.name == "new-uuid"

    def test_returns_none_when_no_sessions(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GROK_HOME", str(tmp_path / "empty"))
        assert grok.locate_session(tmp_path) is None

    def test_session_from_hook_resolves_only_native_history(
        self, tmp_path, monkeypatch,
    ):
        grok_home = tmp_path / "grok-home"
        history = (
            grok_home / "sessions" / "project" / "session-1"
            / "chat_history.jsonl"
        )
        history.parent.mkdir(parents=True)
        history.write_text("{}\n")
        monkeypatch.setenv("GROK_HOME", str(grok_home))

        assert grok.session_from_hook({
            "transcript_path": str(history),
        }) == history.parent
        other = history.parent / "other.jsonl"
        other.write_text("{}\n")
        assert grok.session_from_hook({
            "transcript_path": str(other),
        }) is None


# ---------------------------------------------------------------------------
# dump_current_session (zero-LLM)
# ---------------------------------------------------------------------------

class TestDumpCurrentSession:
    def test_writes_markdown_batches(self, tmp_path, monkeypatch):
        cwd = tmp_path / "project"
        cwd.mkdir()
        raw_root = tmp_path / "raw"

        grok_home = tmp_path / "grok-home"
        encoded = urllib_encode(str(cwd.resolve()))
        session_dir = grok_home / "sessions" / encoded / "abc12345-dead-beef"
        session_dir.mkdir(parents=True)
        (session_dir / "chat_history.jsonl").write_text(FIXTURE.read_text())
        (session_dir / "summary.json").write_text('{"id": "abc12345"}')

        monkeypatch.setenv("GROK_HOME", str(grok_home))
        written = grok.dump_current_session(raw_root, cwd)
        assert len(written) >= 1
        assert all(w.parent.name == "grok-session" for w in written)
        content = written[0].read_text()
        assert "source: grok-session" in content
        assert "User:" in content or "Assistant:" in content

    def test_returns_empty_when_no_session(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GROK_HOME", str(tmp_path / "empty"))
        assert grok.dump_current_session(tmp_path / "raw", tmp_path) == []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def urllib_encode(path: str) -> str:
    import urllib.parse
    return urllib.parse.quote(path, safe="")
