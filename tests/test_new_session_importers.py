"""Transcript adapters added for Qoder, Copilot CLI, and Command Code."""
from __future__ import annotations

import json
import os
from pathlib import Path

from typer.testing import CliRunner

from lorekeep.cli import app
from lorekeep.importer import commandcode, copilot, qoder

runner = CliRunner()


def _write_jsonl(path: Path, records: list[object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )
    return path


def test_qoder_transcript_parses_messages_and_tools(
    isolated_home: Path, monkeypatch,
):
    root = isolated_home / "qoder-home"
    monkeypatch.setenv("QODER_CONFIG_DIR", str(root))
    transcript = _write_jsonl(root / "projects" / "p" / "transcript" / "s.jsonl", [
        {"type": "session_meta", "cwd": "/repo"},
        {"type": "user", "message": {"role": "user", "content": "Design it"}},
        {"type": "assistant", "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "Use events"}],
            "tool_calls": [{"name": "write_file"}],
        }},
    ])

    turns = qoder.parse_transcript(transcript)
    assert len(turns) == 1
    assert turns[0].user_content == "Design it"
    assert turns[0].assistant_text == "Use events"
    assert turns[0].tool_calls == ["write_file"]
    assert qoder.session_from_hook({"transcript_path": str(transcript)}) == transcript


def test_qoder_parser_skips_meta_and_tool_result_user_records(
    isolated_home: Path, monkeypatch,
):
    root = isolated_home / "qoder-home"
    monkeypatch.setenv("QODER_CONFIG_DIR", str(root))
    transcript = _write_jsonl(root / "projects" / "p" / "s.jsonl", [
        {"type": "user", "isMeta": True, "message": {
            "role": "user", "content": "internal reminder",
        }},
        {"type": "user", "message": {
            "role": "user", "content": [{"type": "text", "text": "Build it"}],
        }},
        {"type": "assistant", "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "name": "write_file"}],
        }},
        {"type": "user", "message": {
            "role": "user",
            "content": [{"type": "tool_result", "content": "secret output"}],
        }},
        {"type": "assistant", "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "Done"}],
        }},
    ])
    turns = qoder.parse_transcript(transcript)
    assert [(turn.user_content, turn.assistant_text) for turn in turns] == [
        ("Build it", "Done"),
    ]
    assert turns[0].tool_calls == ["write_file"]


def test_qoder_locate_scans_past_header_for_matching_cwd(
    isolated_home: Path, monkeypatch,
):
    root = isolated_home / "qoder-home"
    monkeypatch.setenv("QODER_CONFIG_DIR", str(root))
    wanted = isolated_home / "wanted"
    other = _write_jsonl(root / "projects" / "p" / "newest.jsonl", [
        {"type": "workspace-directories"},
        {"type": "user", "cwd": str(isolated_home / "other")},
    ])
    matching = _write_jsonl(root / "projects" / "p" / "matching.jsonl", [
        {"type": "workspace-directories"},
        {"type": "user", "cwd": str(wanted)},
    ])
    other.touch()
    assert qoder.locate_session(wanted) == matching


def test_qoder_hook_rejects_transcript_outside_agent_home(
    tmp_path: Path, isolated_home: Path, monkeypatch,
):
    root = isolated_home / "qoder-home"
    monkeypatch.setenv("QODER_CONFIG_DIR", str(root))
    outside = _write_jsonl(tmp_path / "secret.jsonl", [
        {"type": "user", "message": {"role": "user", "content": "secret"}},
    ])
    assert qoder.session_from_hook({"transcript_path": str(outside)}) is None


def test_qoder_dump_current_session_and_missing_home(
    tmp_path: Path, isolated_home: Path, monkeypatch,
):
    root = isolated_home / "qoder-home"
    monkeypatch.setenv("QODER_CONFIG_DIR", str(root))
    assert qoder.locate_session() is None
    assert qoder.session_from_hook({"session_id": "missing"}) is None
    assert qoder.dump_current_session(tmp_path / "raw") == []

    transcript = _write_jsonl(root / "projects" / "p" / "q1.jsonl", [
        {"type": "user", "message": {"role": "user", "content": "Plan it"}},
        {"type": "assistant", "message": {
            "role": "assistant", "content": "Use a queue",
        }},
    ])
    assert qoder.locate_session() == transcript
    assert qoder.session_from_hook({"session_id": "q1"}) == transcript
    assert qoder.session_from_hook({"session_id": "missing"}) is None
    written = qoder.dump_current_session(tmp_path / "raw", dry_run=True)
    assert len(written) == 1
    assert not (tmp_path / "raw").exists()


def test_copilot_transcript_parses_persisted_event_stream(
    isolated_home: Path, monkeypatch,
):
    root = isolated_home / "copilot-home"
    monkeypatch.setenv("COPILOT_HOME", str(root))
    transcript = _write_jsonl(root / "session-state" / "cp-1" / "events.jsonl", [
        {"type": "user.message", "data": {"content": "Fix auth"}},
        {"type": "assistant.message", "data": {
            "content": "Rotate the token.",
            "toolRequests": [{"name": "edit"}],
        }},
        {"type": "tool.execution_start", "data": {"toolName": "test"}},
    ])

    turns = copilot.parse_transcript(transcript)
    assert [(turn.user_content, turn.assistant_text) for turn in turns] == [
        ("Fix auth", "Rotate the token."),
    ]
    assert turns[0].tool_calls == ["edit", "test"]
    assert copilot.session_from_hook({"session_id": "cp-1"}) == transcript


def test_copilot_hook_rejects_session_id_path_traversal(
    isolated_home: Path, monkeypatch,
):
    root = isolated_home / "copilot-home"
    monkeypatch.setenv("COPILOT_HOME", str(root))
    assert copilot.session_from_hook({"session_id": "../../outside"}) is None
    assert copilot.session_from_hook({"session_id": "missing"}) is None


def test_copilot_locate_by_cwd_tolerates_bad_candidates_and_dumps(
    tmp_path: Path, isolated_home: Path, monkeypatch,
):
    root = isolated_home / "copilot-home"
    monkeypatch.setenv("COPILOT_HOME", str(root))
    assert copilot.locate_session() is None
    assert copilot.dump_current_session(tmp_path / "raw") == []

    bad = _write_jsonl(root / "session-state" / "bad" / "events.jsonl", [
        ["unexpected", "header"],
    ])
    wanted = tmp_path / "repo"
    transcript = _write_jsonl(
        root / "session-state" / "cp-match" / "events.jsonl", [
            {"type": "session.start", "data": {"context": {"cwd": str(wanted)}}},
            {"type": "user.message", "data": {
                "content": [{"text": "Fix login"}, {"ignored": True}],
            }},
            {"type": "assistant.message", "data": {
                "message": {"text": "Rotate keys"},
                "toolRequests": [None, {"toolName": "edit"}],
            }},
        ],
    )
    bad.touch()

    assert copilot.locate_session(wanted) == transcript
    assert copilot.session_from_hook({"cwd": str(wanted)}) == transcript
    written = copilot.dump_current_session(
        tmp_path / "raw", wanted, namespace="cp-test",
    )
    assert len(written) == 1
    assert (tmp_path / "raw" / "cp-test").is_dir()


def test_copilot_parser_skips_invalid_json_and_non_object_records(tmp_path: Path):
    transcript = tmp_path / "events.jsonl"
    transcript.write_text(
        "not-json\n"
        + json.dumps(["not", "an", "event"])
        + "\n"
        + json.dumps({"type": "user.message", "data": {"content": {
            "content": "Question",
        }}})
        + "\n"
        + json.dumps({"type": "tool.execution_start", "data": {
            "name": "shell",
        }})
        + "\n",
    )
    turns = copilot.parse_transcript(transcript)
    assert turns[0].user_content == "Question"
    assert turns[0].tool_calls == ["shell"]


def test_commandcode_transcript_uses_native_hook_path(
    isolated_home: Path, monkeypatch,
):
    root = isolated_home / "command-home"
    monkeypatch.setenv("COMMANDCODE_HOME", str(root))
    transcript = _write_jsonl(root / "history" / "cmd.jsonl", [
        {"role": "user", "content": "Add a cache"},
        {"role": "assistant", "content": "Use SQLite."},
    ])

    turns = commandcode.parse_transcript(transcript)
    assert turns[0].user_content == "Add a cache"
    assert turns[0].assistant_text == "Use SQLite."
    assert commandcode.session_from_hook({
        "transcript_path": str(transcript),
    }) == transcript


def test_commandcode_parser_captures_native_hyphenated_tool_call(
    isolated_home: Path, monkeypatch,
):
    root = isolated_home / "command-home"
    monkeypatch.setenv("COMMANDCODE_HOME", str(root))
    transcript = _write_jsonl(root / "projects" / "p" / "cmd.jsonl", [
        {"role": "user", "content": [{"type": "text", "text": "Edit it"}]},
        {"role": "assistant", "content": [
            {"type": "tool-call", "toolName": "write_file", "input": {}},
            {"type": "text", "text": "Updated"},
        ]},
    ])
    turns = commandcode.parse_transcript(transcript)
    assert turns[0].assistant_text == "Updated"
    assert turns[0].tool_calls == ["write_file"]


def test_commandcode_locate_fallback_and_dump_current_session(
    tmp_path: Path, isolated_home: Path, monkeypatch,
):
    root = isolated_home / "command-home"
    monkeypatch.setenv("COMMANDCODE_HOME", str(root))
    assert commandcode.locate_session() is None
    assert commandcode.dump_current_session(tmp_path / "raw") == []

    transcript = _write_jsonl(root / "projects" / "p" / "cmd-1.jsonl", [
        {"type": "user", "message": "Ship it"},
        {"type": "assistant", "content": "Done"},
    ])
    assert commandcode.locate_session() == transcript
    assert commandcode.session_from_hook({}) == transcript
    written = commandcode.dump_current_session(
        tmp_path / "raw", namespace="cmd-test",
    )
    assert len(written) == 1
    assert commandcode.session_key(transcript) == "cmd-1"


def test_commandcode_locate_skips_sidecar_files(
    tmp_path: Path, isolated_home: Path, monkeypatch,
):
    """Checkpoints/prompt sidecars newer than the transcript are not picked."""
    root = isolated_home / "command-home"
    monkeypatch.setenv("COMMANDCODE_HOME", str(root))
    transcript = _write_jsonl(root / "projects" / "p" / "cmd-1.jsonl", [
        {"type": "user", "message": "Ship it"},
        {"type": "assistant", "content": "Done"},
    ])
    sidecars = [
        root / "projects" / "p" / "cmd-1.checkpoints.jsonl",
        root / "projects" / "p" / "cmd-1.prompts.jsonl",
    ]
    for sidecar in sidecars:
        sidecar.write_text(json.dumps({"checkpoint": 1}) + "\n")
        os.utime(sidecar, (2000000000, 2000000000))

    assert commandcode.locate_session() == transcript


def test_manual_qoder_import_reuses_zero_llm_adapter(
    tmp_path: Path, isolated_home: Path, monkeypatch,
):
    home = tmp_path / "lorekeep-home"
    home.mkdir()
    (home / "config.yaml").write_text("install_source: local\n")
    qoder_home = isolated_home / "qoder-home"
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    monkeypatch.setenv("QODER_CONFIG_DIR", str(qoder_home))
    transcript = _write_jsonl(qoder_home / "projects" / "p" / "transcript" / "s.jsonl", [
        {"type": "user", "message": {"role": "user", "content": "Pick storage"}},
        {"type": "assistant", "message": {"role": "assistant", "content": "Use SQLite"}},
    ])

    result = runner.invoke(app, [
        "import", "--from", "qoder", "--session-path", str(transcript),
    ])

    assert result.exit_code == 0, result.stdout
    page = next((home / "raw" / "qoder-session").glob("*.md"))
    assert "Pick storage" in page.read_text()


def test_manual_commandcode_import_dry_run_does_not_write(
    tmp_path: Path, isolated_home: Path, monkeypatch,
):
    home = tmp_path / "lorekeep-home"
    home.mkdir()
    (home / "config.yaml").write_text("install_source: local\n")
    command_home = isolated_home / "command-home"
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    monkeypatch.setenv("COMMANDCODE_HOME", str(command_home))
    transcript = _write_jsonl(command_home / "history" / "s.jsonl", [
        {"role": "user", "content": "Pick storage"},
        {"role": "assistant", "content": "Use SQLite"},
    ])

    result = runner.invoke(app, [
        "import", "--from", "cmd", "--session-path", str(transcript),
        "--dry-run",
    ])

    assert result.exit_code == 0, result.stdout
    assert "would import 1" in result.stdout
    assert not (home / "raw").exists()


def test_manual_zero_llm_import_rejects_quick_flag(
    tmp_path: Path, monkeypatch,
):
    home = tmp_path / "lorekeep-home"
    home.mkdir()
    (home / "config.yaml").write_text("install_source: local\n")
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    result = runner.invoke(app, ["import", "--from", "qoder", "--quick"])
    assert result.exit_code == 1
    assert "already zero-llm" in result.stdout.lower()
