"""Targeted coverage fill for importer modules: cursor, codex, claude, opencode.

Covers edge-case branches not exercised by the existing test files:
corrupt JSON, non-dict blobs, int-typed roles, dry-run, provider-None,
summarize exceptions, manifest corruption, darwin path, XDG path.
"""
import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from lorekeep.compile.providers import FakeProvider
from lorekeep.importer.cursor import (
    _bubble_role,
    _bubble_text,
    default_cursor_db,
    find_cursor_state_db,
    import_cursor,
    load_composer_conversations,
    parse_composer_turns,
)
from lorekeep.importer.codex import (
    _extract_message_text,
    find_current_session as codex_find_current,
    import_codex,
    import_memories as codex_import_memories,
    import_session_deep as codex_import_session_deep,
    parse_rollout,
)
from lorekeep.importer.claude import (
    load_import_manifest,
    parse_transcript,
    import_session_deep as claude_import_session_deep,
)
from lorekeep.importer.opencode import (
    _opencode_data_dir,
    import_opencode,
    import_session_deep as oc_import_session_deep,
)


# ======================================================================
# Cursor importer coverage
# ======================================================================

def _blob_with_turns() -> dict:
    return {
        "composerId": "cccc0000-1111-2222-3333-444455556666",
        "text": "",
        "createdAt": 3000,
        "conversationMap": {
            "bubble_001": {"type": "user", "text": "hello world"},
            "bubble_002": {"type": "assistant", "text": "hi there"},
        },
    }


def test_bubble_role_int_values():
    """_bubble_role accepts numeric types: 1=user, 2=assistant."""
    assert _bubble_role({"type": 1}) == "user"
    assert _bubble_role({"role": 2}) == "assistant"


def test_bubble_role_unknown_returns_none():
    assert _bubble_role({"type": 99}) is None
    assert _bubble_role({}) is None


def test_bubble_text_all_fields_empty():
    assert _bubble_text({"text": "", "richText": "", "content": ""}) == ""
    assert _bubble_text({"text": "   "}) == ""


def test_bubble_text_rich_text_fallback():
    assert _bubble_text({"richText": "from rich"}) == "from rich"
    assert _bubble_text({"content": "from content"}) == "from content"


def test_parse_assistant_only_no_user():
    """Assistant-only bubbles (no preceding user) still produce a turn."""
    blob = {
        "conversationMap": {
            "b1": {"type": "assistant", "text": "just assistant"},
        },
        "text": "",
    }
    turns = parse_composer_turns(blob)
    assert len(turns) == 1
    assert turns[0].user_content == ""
    assert "just assistant" in turns[0].assistant_text


def test_parse_skips_empty_text_bubble():
    blob = {
        "conversationMap": {
            "b1": {"type": "user", "text": "  "},
            "b2": {"type": "assistant", "text": "reply"},
        },
    }
    turns = parse_composer_turns(blob)
    # The empty-text user bubble is skipped; assistant text creates a turn
    assert len(turns) == 1
    assert "reply" in turns[0].assistant_text


def test_parse_tool_name_captured():
    blob = {
        "conversationMap": {
            "b1": {"type": "user", "text": "run tests"},
            "b2": {"type": "assistant", "text": "running", "toolName": "pytest"},
            "b3": {"type": "assistant", "text": "done", "name": "flake8"},
        },
    }
    turns = parse_composer_turns(blob)
    assert "pytest" in turns[0].tool_calls
    assert "flake8" in turns[0].tool_calls


def test_parse_order_key_no_created_at_fallback():
    """Bubbles without createdAt fall back to numeric bubble-id ordering."""
    blob = {
        "conversationMap": {
            "bubble_010": {"type": "assistant", "text": "second"},
            "bubble_001": {"type": "user", "text": "first"},
        },
    }
    turns = parse_composer_turns(blob)
    assert turns[0].user_content == "first"


def test_load_skips_corrupt_json(tmp_path: Path):
    db = tmp_path / "state.vscdb"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO cursorDiskKV VALUES (?, ?)",
                ("composerData:bad1", "not-json{"))
    con.execute("INSERT INTO cursorDiskKV VALUES (?, ?)",
                ("composerData:bad2", "[1,2,3]"))  # JSON but not dict
    con.execute("INSERT INTO cursorDiskKV VALUES (?, ?)",
                ("composerData:good", json.dumps(_blob_with_turns())))
    con.commit()
    con.close()
    blobs = load_composer_conversations(db)
    assert len(blobs) == 1


def test_find_cursor_state_db_no_env(monkeypatch, tmp_path: Path):
    """When CURSOR_STATE_DB is unset, checks the default location."""
    monkeypatch.delenv("CURSOR_STATE_DB", raising=False)
    fake_db = tmp_path / "state.vscdb"
    fake_db.write_text("")
    monkeypatch.setattr("lorekeep.importer.cursor.default_cursor_db", lambda: fake_db)
    assert find_cursor_state_db() == fake_db


def test_find_cursor_state_db_default_missing(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("CURSOR_STATE_DB", raising=False)
    monkeypatch.setattr("lorekeep.importer.cursor.default_cursor_db",
                        lambda: tmp_path / "nonexistent.vscdb")
    assert find_cursor_state_db() is None


def test_default_cursor_db():
    p = default_cursor_db()
    assert p.name == "state.vscdb"


def test_cursor_config_dir_darwin(monkeypatch):
    """On macOS the config dir is under Library/Application Support."""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: Path("/Users/test"))
    from lorekeep.importer.cursor import _cursor_config_dir
    d = _cursor_config_dir()
    assert "Library" in str(d) and "Cursor" in str(d)


def test_import_cursor_dry_run(tmp_path: Path):
    db = tmp_path / "state.vscdb"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO cursorDiskKV VALUES (?, ?)",
                ("composerData:x", json.dumps(_blob_with_turns())))
    con.commit()
    con.close()
    raw = tmp_path / "raw"
    provider = FakeProvider(responses=["# x\n"] * 10)
    result = import_cursor(raw_root=raw, db_path=db, provider=provider, dry_run=True)
    assert len(result["session"]) >= 1
    # dry_run does not write files
    assert not (raw / "cursor-session").exists()


def test_import_cursor_summarize_exception(tmp_path: Path):
    """When summarize raises, error markdown is written instead of crashing."""
    db = tmp_path / "state.vscdb"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO cursorDiskKV VALUES (?, ?)",
                ("composerData:x", json.dumps(_blob_with_turns())))
    con.commit()
    con.close()
    raw = tmp_path / "raw"
    provider = FakeProvider(responses=[])  # empty → will raise on call

    with patch("lorekeep.importer.cursor.summarize_batch",
               side_effect=RuntimeError("LLM exploded")):
        result = import_cursor(raw_root=raw, db_path=db, provider=provider)
    assert len(result["session"]) >= 1
    md = result["session"][0].read_text()
    assert "Error summarizing" in md


# ======================================================================
# Codex importer coverage
# ======================================================================

def _write_codex_rollout(path: Path, cwd: str, turns: list[dict]) -> None:
    lines = [json.dumps({
        "timestamp": "2026-06-28T10:00:00.000Z",
        "type": "session_meta",
        "payload": {"session_id": "s1", "cwd": cwd},
    })]
    for t in turns:
        lines.append(json.dumps(t))
    path.write_text("\n".join(lines) + "\n")


def test_extract_message_text_string():
    assert _extract_message_text("plain string") == "plain string"


def test_extract_message_text_non_dict_block():
    """Non-dict blocks in the content list are silently skipped."""
    result = _extract_message_text([{"text": "hello"}, "not-a-dict", 42, {"text": "world"}])
    assert "hello" in result and "world" in result


def test_parse_rollout_corrupt_line(tmp_path: Path):
    rollout = tmp_path / "rollout-bad.jsonl"
    meta = json.dumps({"type": "session_meta", "payload": {"cwd": str(tmp_path)}})
    rollout.write_text(f"{meta}\nnot-valid-json{{\n\n")
    assert parse_rollout(rollout) == []


def test_parse_rollout_multi_turn(tmp_path: Path):
    rollout = tmp_path / "rollout-multi.jsonl"
    _write_codex_rollout(rollout, str(tmp_path), [
        {"type": "response_item", "payload": {"type": "message", "role": "user",
         "content": [{"type": "input_text", "text": "q1"}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant",
         "content": [{"type": "output_text", "text": "a1"}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "user",
         "content": [{"type": "input_text", "text": "## My request for Codex:\nq2"}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant",
         "content": [{"type": "output_text", "text": "a2"}]}},
    ])
    turns = parse_rollout(rollout)
    assert len(turns) == 2
    assert turns[1].user_content == "q2"


def test_parse_rollout_tool_calls(tmp_path: Path):
    rollout = tmp_path / "rollout-tools.jsonl"
    _write_codex_rollout(rollout, str(tmp_path), [
        {"type": "response_item", "payload": {"type": "message", "role": "user",
         "content": [{"type": "input_text", "text": "run tests"}]}},
        {"type": "response_item", "payload": {"type": "function_call", "name": "pytest"}},
        {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "mypy"}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant",
         "content": [{"type": "output_text", "text": "all pass"}]}},
    ])
    turns = parse_rollout(rollout)
    assert "pytest" in turns[0].tool_calls
    assert "mypy" in turns[0].tool_calls


def test_codex_find_current_session_corrupt(tmp_path: Path, monkeypatch):
    """Rollout with corrupt/empty first line is skipped without crashing."""
    codex_home = tmp_path / "codex"
    sessions_dir = codex_home / "sessions" / "2026" / "06" / "28"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "rollout-bad1.jsonl").write_text("\n\n")  # empty first line
    (sessions_dir / "rollout-bad2.jsonl").write_text("not-json\n")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    assert codex_find_current(cwd=tmp_path) is None


def test_codex_import_memories_dry_run(tmp_path: Path, monkeypatch):
    codex_home = tmp_path / "codex"
    mem = codex_home / "memories"
    mem.mkdir(parents=True)
    (mem / "fact.md").write_text("# Fact\n")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    raw = tmp_path / "raw"
    written = codex_import_memories(raw, dry_run=True)
    assert len(written) == 1
    assert not (raw / "codex-memory").exists()


def test_codex_import_session_deep_dry_run(tmp_path: Path):
    rollout = tmp_path / "rollout-test.jsonl"
    _write_codex_rollout(rollout, str(tmp_path), [
        {"type": "response_item", "payload": {"type": "message", "role": "user",
         "content": [{"type": "input_text", "text": "hello"}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant",
         "content": [{"type": "output_text", "text": "world"}]}},
    ])
    raw = tmp_path / "raw"
    provider = FakeProvider(responses=["# s\n"] * 10)
    written = codex_import_session_deep(rollout, raw, provider=provider, dry_run=True)
    assert len(written) >= 1
    assert not (raw / "codex-session").exists()


def test_codex_import_session_deep_no_provider(tmp_path: Path):
    rollout = tmp_path / "rollout-test.jsonl"
    _write_codex_rollout(rollout, str(tmp_path), [
        {"type": "response_item", "payload": {"type": "message", "role": "user",
         "content": [{"type": "input_text", "text": "hello"}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant",
         "content": [{"type": "output_text", "text": "world"}]}},
    ])
    raw = tmp_path / "raw"
    written = codex_import_session_deep(rollout, raw, provider=None)
    assert len(written) >= 1
    md = written[0].read_text()
    assert "Error summarizing" in md


def test_codex_import_session_deep_summarize_error(tmp_path: Path):
    rollout = tmp_path / "rollout-test.jsonl"
    _write_codex_rollout(rollout, str(tmp_path), [
        {"type": "response_item", "payload": {"type": "message", "role": "user",
         "content": [{"type": "input_text", "text": "hello"}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant",
         "content": [{"type": "output_text", "text": "world"}]}},
    ])
    raw = tmp_path / "raw"
    provider = FakeProvider(responses=["# s\n"] * 10)
    with patch("lorekeep.importer.codex.summarize_batch",
               side_effect=ValueError("boom")):
        written = codex_import_session_deep(rollout, raw, provider=provider)
    assert len(written) >= 1
    assert "Error summarizing" in written[0].read_text()


# ======================================================================
# Claude importer coverage
# ======================================================================

def test_parse_transcript_corrupt_line(tmp_path: Path):
    """Corrupt JSON lines are silently skipped."""
    tf = tmp_path / "transcript.jsonl"
    good = json.dumps({"role": "user", "message": {"role": "user", "content": "hi"}})
    tf.write_text(f"not-json\n{good}\n")
    turns = parse_transcript(tf)
    assert len(turns) == 1


def test_parse_transcript_nested_role_and_list_content(tmp_path: Path):
    """Nested message.role + list content blocks are parsed correctly."""
    tf = tmp_path / "transcript.jsonl"
    lines = [
        json.dumps({
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "user question"}],
            }
        }),
        json.dumps({
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "assistant answer"}],
            }
        }),
    ]
    tf.write_text("\n".join(lines) + "\n")
    turns = parse_transcript(tf)
    assert len(turns) == 1
    assert "user question" in turns[0].user_content
    assert "assistant answer" in turns[0].assistant_text


def test_load_import_manifest_corrupt(tmp_path: Path):
    """Corrupt manifest JSON returns empty dict."""
    raw = tmp_path / "raw"
    ns_dir = raw / "ns"
    ns_dir.mkdir(parents=True)
    (ns_dir / ".import-manifest.json").write_text("not-json{")
    assert load_import_manifest(raw, "ns") == {}


def test_claude_import_session_deep_no_transcripts(tmp_path: Path):
    """Session dir with no .jsonl files returns []."""
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    raw = tmp_path / "raw"
    result = claude_import_session_deep(session_dir, raw, provider=FakeProvider(responses=[]))
    assert result == []


def test_claude_import_session_deep_no_provider(tmp_path: Path):
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    tf = session_dir / "t1.jsonl"
    tf.write_text(json.dumps({
        "role": "user",
        "message": {"role": "user", "content": "hi"},
    }) + "\n" + json.dumps({
        "role": "assistant",
        "message": {"role": "assistant",
                     "content": [{"type": "text", "text": "hello"}]},
    }) + "\n")
    raw = tmp_path / "raw"
    result = claude_import_session_deep(session_dir, raw, provider=None)
    assert len(result) >= 1
    assert "Error summarizing" in result[0].read_text()


# ======================================================================
# opencode importer coverage
# ======================================================================

def test_opencode_data_dir_xdg(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert _opencode_data_dir() == tmp_path / "xdg" / "opencode"


def _build_oc_db(db_path: Path, cwd: str, session_id: str = "ses_test001") -> str:
    con = sqlite3.connect(str(db_path))
    con.executescript("""
        CREATE TABLE project (id TEXT PRIMARY KEY, worktree TEXT);
        CREATE TABLE session (
            id TEXT PRIMARY KEY, project_id TEXT,
            time_created INTEGER, time_updated INTEGER, data TEXT
        );
        CREATE TABLE message (
            id TEXT PRIMARY KEY, session_id TEXT,
            time_created INTEGER, data TEXT
        );
        CREATE TABLE part (
            id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT,
            time_created INTEGER, data TEXT
        );
    """)
    con.execute("INSERT INTO project VALUES (?, ?)", ("proj_test", cwd))
    con.execute("INSERT INTO session VALUES (?, ?, ?, ?, ?)",
                (session_id, "proj_test", 1000, 2000,
                 json.dumps({"role": "system"})))
    # Two user/assistant pairs → exercises flush of previous turn
    for idx, (role, text) in enumerate([("user", "q1"), ("assistant", "a1"),
                                         ("user", "q2"), ("assistant", "a2")]):
        mid = f"msg_{idx}"
        con.execute("INSERT INTO message VALUES (?, ?, ?, ?)",
                    (mid, session_id, 1100 + idx, json.dumps({"role": role})))
        con.execute("INSERT INTO part VALUES (?, ?, ?, ?, ?)",
                    (f"part_{idx}", mid, session_id, 1100 + idx,
                     json.dumps({"type": "text", "text": text})))
    con.commit()
    con.close()
    return session_id


def test_oc_find_current_session_sqlite_error(tmp_path: Path, monkeypatch):
    """If the DB is corrupt, find_current_session returns None."""
    bad_db = tmp_path / "bad.db"
    bad_db.write_text("not a database")
    monkeypatch.setattr("lorekeep.importer.opencode._opencode_db", lambda: bad_db)
    from lorekeep.importer.opencode import find_current_session
    assert find_current_session(cwd=tmp_path) is None


def test_oc_import_session_deep_dry_run(tmp_path: Path):
    db = tmp_path / "oc.db"
    sid = _build_oc_db(db, str(tmp_path))
    raw = tmp_path / "raw"
    provider = FakeProvider(responses=["# s\n"] * 10)
    written = oc_import_session_deep(sid, raw, provider=provider, db_path=db, dry_run=True)
    assert len(written) >= 1
    assert not (raw / "opencode-session").exists()


def test_oc_import_session_deep_no_provider(tmp_path: Path):
    db = tmp_path / "oc.db"
    sid = _build_oc_db(db, str(tmp_path))
    raw = tmp_path / "raw"
    written = oc_import_session_deep(sid, raw, provider=None, db_path=db)
    assert len(written) >= 1
    assert "Error summarizing" in written[0].read_text()


def test_oc_import_session_deep_session_not_found(tmp_path: Path):
    db = tmp_path / "oc.db"
    _build_oc_db(db, str(tmp_path))
    raw = tmp_path / "raw"
    provider = FakeProvider(responses=["# s\n"] * 10)
    assert oc_import_session_deep("nonexistent", raw, provider=provider, db_path=db) == []


def test_oc_import_session_deep_summarize_error(tmp_path: Path):
    db = tmp_path / "oc.db"
    sid = _build_oc_db(db, str(tmp_path))
    raw = tmp_path / "raw"
    provider = FakeProvider(responses=["# s\n"] * 10)
    with patch("lorekeep.importer.opencode.summarize_batch",
               side_effect=RuntimeError("fail")):
        written = oc_import_session_deep(sid, raw, provider=provider, db_path=db)
    assert len(written) >= 1
    assert "Error summarizing" in written[0].read_text()


def test_oc_import_session_deep_no_turns(tmp_path: Path):
    """Session exists but has no messages → empty turns → []."""
    db = tmp_path / "oc.db"
    con = sqlite3.connect(str(db))
    con.executescript("""
        CREATE TABLE project (id TEXT PRIMARY KEY, worktree TEXT);
        CREATE TABLE session (
            id TEXT PRIMARY KEY, project_id TEXT,
            time_created INTEGER, time_updated INTEGER, data TEXT
        );
        CREATE TABLE message (
            id TEXT PRIMARY KEY, session_id TEXT,
            time_created INTEGER, data TEXT
        );
        CREATE TABLE part (
            id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT,
            time_created INTEGER, data TEXT
        );
    """)
    con.execute("INSERT INTO project VALUES (?, ?)", ("p1", str(tmp_path)))
    con.execute("INSERT INTO session VALUES (?, ?, ?, ?, ?)",
                ("empty-ses", "p1", 1000, 2000, "{}"))
    con.commit()
    con.close()
    raw = tmp_path / "raw"
    provider = FakeProvider(responses=["# s\n"] * 10)
    assert oc_import_session_deep("empty-ses", raw, provider=provider, db_path=db) == []
