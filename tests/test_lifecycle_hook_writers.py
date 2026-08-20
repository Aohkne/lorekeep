"""Native hook event names, schemas, scopes, and merge preservation."""
from __future__ import annotations

import json
from pathlib import Path

from lorekeep.integrations import commandcode, copilot, cursor, grok, qoder


def test_grok_writes_nested_session_end_hook(tmp_path: Path):
    path = grok.write_hook(tmp_path, "/opt/lore keep/python", ["-m", "lorekeep.cli"])
    data = json.loads(path.read_text())
    handler = data["hooks"]["SessionEnd"][0]["hooks"][0]
    assert handler["type"] == "command"
    assert "'/opt/lore keep/python'" in handler["command"]
    assert handler["timeout"] == 30


def test_qoder_user_settings_preserve_mcp_when_hook_is_added(
    tmp_path: Path, isolated_home: Path,
):
    qoder.write_config(
        tmp_path, "lorekeep", ["serve"], "me", scope="user",
    )
    path = qoder.write_hook(
        tmp_path, "python", ["-m", "lorekeep.cli"], scope="user",
    )
    data = json.loads(path.read_text())
    assert "lorekeep" in data["mcpServers"]
    assert "SessionEnd" in data["hooks"]
    handler = data["hooks"]["SessionEnd"][0]["hooks"][0]
    assert handler["command"] == "python -m lorekeep.cli"
    assert "args" not in handler


def test_copilot_writes_flat_user_hook_and_skips_project(
    tmp_path: Path, isolated_home: Path,
):
    assert copilot.write_hook(tmp_path, "python", ["hook"]) is None
    path = copilot.write_hook(
        tmp_path, "python", ["hook"], scope="user",
    )
    data = json.loads(path.read_text())
    assert data["version"] == 1
    handler = data["hooks"]["sessionEnd"][0]
    assert handler == {
        "type": "command", "command": "python hook", "timeoutSec": 5,
    }


def test_copilot_user_hook_honors_custom_home(
    tmp_path: Path, monkeypatch,
):
    custom = tmp_path / "copilot-home"
    monkeypatch.setenv("COPILOT_HOME", str(custom))
    path = copilot.write_hook(
        tmp_path, "python", ["hook"], scope="user",
    )
    assert path == custom / "hooks" / "lorekeep.json"


def test_cursor_project_session_end_is_supported(tmp_path: Path):
    path = cursor.write_hook(tmp_path, "python", ["hook"])
    assert path == tmp_path / ".cursor" / "hooks.json"
    assert "sessionEnd" in json.loads(path.read_text())["hooks"]


def test_commandcode_writes_debounced_turn_end_fallback(tmp_path: Path):
    path = commandcode.write_hook(
        tmp_path, "python", ["-m", "lorekeep.cli"],
    )
    data = json.loads(path.read_text())
    handler = data["hooks"]["Stop"][0]["hooks"][0]
    assert handler["type"] == "command"
    assert handler["timeout"] == 30


def test_qoder_rewrite_migrates_args_form_handler(tmp_path: Path):
    """A pre-fix args-form lorekeep handler is replaced by the shell form."""
    path = tmp_path / ".qoder" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "hooks": {"SessionEnd": [{
            "hooks": [{
                "type": "command", "command": "/old/python",
                "args": ["-m", "lorekeep.cli", "hook", "--agent", "qoder"],
                "timeout": 30,
            }]},
        ]},
    }))

    qoder.write_hook(tmp_path, "python", ["-m", "lorekeep.cli", "hook"])

    groups = json.loads(path.read_text())["hooks"]["SessionEnd"]
    assert len(groups) == 1
    handler = groups[0]["hooks"][0]
    assert handler["command"] == "python -m lorekeep.cli hook"
    assert "args" not in handler


def test_hook_rewrite_preserves_third_party_handler(tmp_path: Path):
    path = tmp_path / ".commandcode" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "hooks": {"Stop": [{
            "hooks": [{"type": "command", "command": "./audit.sh"}],
        }]},
    }))
    commandcode.write_hook(tmp_path, "python", ["-m", "lorekeep.cli", "hook"])
    groups = json.loads(path.read_text())["hooks"]["Stop"]
    assert groups[0]["hooks"][0]["command"] == "./audit.sh"
    assert len(groups) == 2


def test_hook_rewrite_does_not_remove_similarly_named_third_party(
    tmp_path: Path,
):
    path = tmp_path / ".commandcode" / "settings.json"
    path.parent.mkdir(parents=True)
    command = "./lorekeep-audit --mode hook"
    path.write_text(json.dumps({
        "hooks": {"Stop": [{
            "hooks": [{"type": "command", "command": command}],
        }]},
    }))

    commandcode.write_hook(tmp_path, "python", ["-m", "lorekeep.cli", "hook"])

    groups = json.loads(path.read_text())["hooks"]["Stop"]
    assert groups[0]["hooks"][0]["command"] == command
