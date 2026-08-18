"""Durable lifecycle ingress, debounce, retry, and targeted import."""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

from lorekeep.config import AgentsConfig
from lorekeep.hook_events import (
    MAX_HOOK_PAYLOAD_BYTES,
    drain_hook_events,
    enqueue_hook_event,
    parse_hook_payload,
)
from lorekeep.integrations.common import resolve_hook_command


def _transcript(path: Path, *, user: str = "Choose an API") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {"type": "session_meta", "cwd": str(path.parent)},
        {"type": "user", "message": {"role": "user", "content": user}},
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Use FastAPI."},
                    {"type": "tool_use", "name": "write_file"},
                ],
            },
        },
    ]
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )
    return path


def test_enqueue_normalizes_aliases_and_uses_private_mode(tmp_path: Path):
    path = enqueue_hook_event(
        tmp_path,
        agent="copilot",
        trigger="session_end",
        raw_payload=json.dumps({
            "sessionId": "copilot-1",
            "workingDirectory": "/work/tree",
            "hookEventName": "sessionEnd",
        }),
        now=10.0,
    )

    data = json.loads(path.read_text())
    assert data["session_id"] == "copilot-1"
    assert data["cwd"] == "/work/tree"
    assert data["native_event"] == "sessionEnd"
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(path.parent.parent.stat().st_mode) == 0o700


def test_enqueue_rejects_wrong_trigger(tmp_path: Path):
    try:
        enqueue_hook_event(
            tmp_path, agent="opencode", trigger="session_end",
        )
    except ValueError as exc:
        assert "idle_fallback" in str(exc)
    else:
        raise AssertionError("wrong trigger was accepted")


def test_enqueue_bounds_long_session_id_filename(tmp_path: Path):
    session_id = "s" * 400
    path = enqueue_hook_event(
        tmp_path, agent="claude", trigger="session_end",
        session_id=session_id,
    )
    assert len(path.name.encode()) < 255
    assert json.loads(path.read_text())["session_id"] == session_id


def test_parse_hook_payload_rejects_invalid_shapes_and_size():
    assert parse_hook_payload("") == {}
    for raw in ("not-json", "[]", "x" * (MAX_HOOK_PAYLOAD_BYTES + 1)):
        try:
            parse_hook_payload(raw)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid hook payload accepted: {raw[:16]!r}")


def test_enqueue_reads_opencode_nested_properties_and_coalesces_session(
    tmp_path: Path,
):
    first = enqueue_hook_event(
        tmp_path,
        agent="opencode",
        trigger="idle_fallback",
        raw_payload=json.dumps({
            "properties": {"sessionID": "session/one", "cwd": "/repo"},
        }),
        now=10.0,
    )
    second = enqueue_hook_event(
        tmp_path,
        agent="opencode",
        trigger="idle_fallback",
        session_id="session/one",
        cwd="/repo",
        now=20.0,
    )

    assert first == second
    assert len(list(first.parent.glob("*.json"))) == 1
    data = json.loads(second.read_text())
    assert data["session_id"] == "session/one"
    assert data["received_at"] == 20.0


def test_drain_without_event_directory_is_empty(tmp_path: Path):
    assert drain_hook_events(
        tmp_path, tmp_path / "raw", AgentsConfig(), now=1.0,
    ).processed == 0


def test_exact_session_end_drains_immediately(
    tmp_path: Path, isolated_home: Path, monkeypatch,
):
    qoder_home = isolated_home / ".qoder"
    monkeypatch.setenv("QODER_CONFIG_DIR", str(qoder_home))
    transcript = _transcript(
        qoder_home / "projects" / "demo" / "transcript" / "q1.jsonl"
    )
    event = enqueue_hook_event(
        tmp_path,
        agent="qoder",
        trigger="session_end",
        raw_payload=json.dumps({
            "session_id": "q1",
            "transcript_path": str(transcript),
            "cwd": str(tmp_path),
            "hook_event_name": "SessionEnd",
        }),
        now=100.0,
    )

    raw = tmp_path / "raw"
    report = drain_hook_events(
        tmp_path, raw, AgentsConfig(), now=100.0,
    )

    assert report.processed == 1
    assert report.written == 1
    assert not event.exists()
    page = next((raw / "qoder-session").glob("*.md"))
    assert "Choose an API" in page.read_text()
    assert "Use FastAPI" in page.read_text()
    assert "write_file" in page.read_text()


def test_turn_end_fallback_waits_for_idle_grace(
    tmp_path: Path, isolated_home: Path, monkeypatch,
):
    command_home = isolated_home / ".commandcode"
    monkeypatch.setenv("COMMANDCODE_HOME", str(command_home))
    transcript = _transcript(command_home / "transcripts" / "cmd-1.jsonl")
    event = enqueue_hook_event(
        tmp_path,
        agent="cmd",
        trigger="turn_end_fallback",
        raw_payload=json.dumps({
            "session_id": "cmd-1", "transcript_path": str(transcript),
        }),
        now=100.0,
    )
    config = AgentsConfig(session_end_idle_seconds=300)

    early = drain_hook_events(tmp_path, tmp_path / "raw", config, now=399.0)
    assert early.deferred == 1
    assert event.exists()

    ready = drain_hook_events(tmp_path, tmp_path / "raw", config, now=400.0)
    assert ready.processed == 1
    assert ready.written == 1
    assert not event.exists()


def test_failed_import_is_retained_with_backoff(
    tmp_path: Path, isolated_home: Path,
):
    event = enqueue_hook_event(
        tmp_path,
        agent="claude",
        trigger="session_end",
        raw_payload=json.dumps({
            "session_id": "missing", "cwd": str(tmp_path),
        }),
        now=10.0,
    )
    config = AgentsConfig(enabled=["claude"])

    failed = drain_hook_events(tmp_path, tmp_path / "raw", config, now=10.0)
    assert failed.failed == 1
    saved = json.loads(event.read_text())
    assert saved["attempts"] == 1
    assert saved["last_attempt_at"] == 10.0

    deferred = drain_hook_events(tmp_path, tmp_path / "raw", config, now=11.0)
    assert deferred.deferred == 1
    assert json.loads(event.read_text())["attempts"] == 1


def test_empty_transcript_is_retained_for_retry(
    tmp_path: Path, isolated_home: Path, monkeypatch,
):
    qoder_home = isolated_home / ".qoder"
    monkeypatch.setenv("QODER_CONFIG_DIR", str(qoder_home))
    transcript = qoder_home / "projects" / "demo" / "empty.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(json.dumps({"type": "session_meta"}) + "\n")
    event = enqueue_hook_event(
        tmp_path,
        agent="qoder",
        trigger="session_end",
        raw_payload=json.dumps({"transcript_path": str(transcript)}),
        now=1.0,
    )

    report = drain_hook_events(
        tmp_path, tmp_path / "raw", AgentsConfig(), now=1.0,
    )

    assert report.failed == 1
    assert json.loads(event.read_text())["attempts"] == 1


def test_disabled_agent_event_is_removed(tmp_path: Path):
    event = enqueue_hook_event(
        tmp_path, agent="claude", trigger="session_end", now=1.0,
    )
    report = drain_hook_events(
        tmp_path, tmp_path / "raw", AgentsConfig(enabled=["codex"]), now=1.0,
    )
    assert report.ignored == 1
    assert not event.exists()


def test_watch_transcripts_off_removes_event(tmp_path: Path):
    event = enqueue_hook_event(
        tmp_path, agent="claude", trigger="session_end", now=1.0,
    )
    report = drain_hook_events(
        tmp_path, tmp_path / "raw",
        AgentsConfig(watch_transcripts=False), now=1.0,
    )
    assert report.ignored == 1
    assert not event.exists()


def test_invalid_queue_record_is_reported_and_retained(tmp_path: Path):
    event = tmp_path / "hook-events" / "claude" / "broken.json"
    event.parent.mkdir(parents=True)
    event.write_text("not-json")
    report = drain_hook_events(
        tmp_path, tmp_path / "raw", AgentsConfig(), now=1.0,
    )
    assert report.failed == 1
    assert event.exists()


def test_resolve_hook_command_uses_current_interpreter_and_pinned_home(
    tmp_path: Path,
):
    command, args = resolve_hook_command("codex", "session_end", tmp_path)
    assert command == sys.executable
    assert args == [
        "-m", "lorekeep.cli", "hook", "--agent", "codex",
        "--trigger", "session_end", "--home", str(tmp_path),
    ]
