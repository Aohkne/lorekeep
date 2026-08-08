import json
from pathlib import Path
from lorekeep.integrations.common import resolve_command, agent_memory_snippet
from lorekeep.integrations import claude_code, cursor, codex, opencode


def test_resolve_command_pypi():
    expected = ["lorekeep", "serve", "--transport", "stdio"]
    assert resolve_command(None) == ("uvx", expected)
    assert resolve_command("pypi") == ("uvx", expected)


def test_resolve_command_local():
    assert resolve_command("local") == (
        "lorekeep", ["serve", "--transport", "stdio"],
    )


def test_resolve_command_git():
    cmd, args = resolve_command("git+https://github.com/x/lorekeep.git")
    assert cmd == "uvx"
    assert args[:2] == ["--from", "git+https://github.com/x/lorekeep.git"]
    assert "serve" in args


def test_claude_writes_mcp_json(tmp_path: Path):
    claude_code.write_config(tmp_path, "uvx", ["lorekeep", "serve", "--transport", "stdio"],
                             ns="teams/backend")
    data = json.loads((tmp_path / ".mcp.json").read_text())
    assert data["mcpServers"]["lorekeep"]["command"] == "uvx"
    assert data["mcpServers"]["lorekeep"]["env"]["LOREKEEP_NS"] == "teams/backend"
    assert data["mcpServers"]["lorekeep"]["env"]["LOREKEEP_AGENT"] == "claude"


def test_cursor_writes_mcp_json(tmp_path: Path):
    cursor.write_config(tmp_path, "uvx", ["lorekeep", "serve", "--transport", "stdio"], ns=None)
    data = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    assert "lorekeep" in data["mcpServers"]


def test_codex_writes_toml(tmp_path: Path):
    codex.write_config(tmp_path, "uvx", ["lorekeep", "serve", "--transport", "stdio"],
                       ns="teams/backend")
    text = (tmp_path / "config.toml").read_text()
    assert "[mcp_servers.lorekeep]" in text
    assert 'command = "uvx"' in text
    assert 'LOREKEEP_NS = "teams/backend"' in text
    assert 'LOREKEEP_AGENT = "codex"' in text


def test_agent_memory_snippet_mentions_provenance():
    s = agent_memory_snippet()
    assert "src" in s and "namespace" in s.lower()


def test_codex_write_is_idempotent(tmp_path: Path):
    codex.write_config(tmp_path, "uvx", ["lorekeep", "serve", "--transport", "stdio"], ns="team/a")
    codex.write_config(tmp_path, "uvx", ["lorekeep", "serve", "--transport", "stdio"], ns="team/b")
    text = (tmp_path / "config.toml").read_text()
    assert text.count("[mcp_servers.lorekeep]") == 1   # replaced, not duplicated
    assert 'LOREKEEP_NS = "team/b"' in text             # updated value


def test_codex_escapes_quotes_in_ns(tmp_path: Path):
    codex.write_config(tmp_path, "uvx", ["lorekeep"], ns='team/"evil')
    text = (tmp_path / "config.toml").read_text()
    assert 'team/\\"evil' in text                      # quote escaped, TOML stays valid


def test_codex_rejects_newline_in_ns(tmp_path: Path):
    import pytest
    with pytest.raises(ValueError):
        codex.write_config(tmp_path, "uvx", ["lorekeep"], ns="team\n[malicious]")


def test_opencode_writes_json(tmp_path: Path):
    opencode.write_config(tmp_path, "uvx", ["lorekeep", "serve", "--transport", "stdio"],
                          ns="teams/backend")
    data = json.loads((tmp_path / "opencode.json").read_text())
    entry = data["mcp"]["lorekeep"]
    assert entry["type"] == "local"
    assert entry["command"] == ["uvx", "lorekeep", "serve", "--transport", "stdio"]
    assert entry["enabled"] is True
    assert entry["environment"]["LOREKEEP_NS"] == "teams/backend"


def test_opencode_no_ns(tmp_path: Path):
    opencode.write_config(tmp_path, "uvx", ["lorekeep", "serve", "--transport", "stdio"], ns=None)
    data = json.loads((tmp_path / "opencode.json").read_text())
    entry = data["mcp"]["lorekeep"]
    assert entry["environment"] == {"LOREKEEP_AGENT": "opencode"}


def test_opencode_idempotent(tmp_path: Path):
    opencode.write_config(tmp_path, "uvx", ["lorekeep"], ns="team/a")
    opencode.write_config(tmp_path, "uvx", ["lorekeep"], ns="team/b")
    data = json.loads((tmp_path / "opencode.json").read_text())
    assert data["mcp"]["lorekeep"]["environment"]["LOREKEEP_NS"] == "team/b"


def test_opencode_preserves_existing_keys(tmp_path: Path):
    existing = {"$schema": "https://opencode.ai/config.json", "model": "anthropic/claude-sonnet-4-5", "mcp": {"other": {"type": "local", "command": ["foo"]}}}
    (tmp_path / "opencode.json").write_text(json.dumps(existing))
    opencode.write_config(tmp_path, "uvx", ["lorekeep", "serve", "--transport", "stdio"], ns=None)
    data = json.loads((tmp_path / "opencode.json").read_text())
    assert data["$schema"] == "https://opencode.ai/config.json"
    assert data["model"] == "anthropic/claude-sonnet-4-5"
    assert "other" in data["mcp"]
    assert "lorekeep" in data["mcp"]


# ── write_config idempotent merge (existing config files) ────────────────


def test_cursor_write_config_merges_existing(tmp_path: Path):
    """write_config preserves existing mcpServers when mcp.json already exists."""
    d = tmp_path / ".cursor"
    d.mkdir(parents=True)
    existing = {"mcpServers": {"other": {"command": "foo"}}}
    (d / "mcp.json").write_text(json.dumps(existing))
    cursor.write_config(tmp_path, "uvx", ["lorekeep", "serve"], ns="ns1")
    data = json.loads((d / "mcp.json").read_text())
    assert "other" in data["mcpServers"]
    assert "lorekeep" in data["mcpServers"]


def test_claude_write_config_merges_existing(tmp_path: Path):
    """write_config preserves existing mcpServers when .mcp.json exists."""
    existing = {"mcpServers": {"other": {"command": "foo"}}}
    (tmp_path / ".mcp.json").write_text(json.dumps(existing))
    claude_code.write_config(tmp_path, "uvx", ["lorekeep", "serve"], ns=None)
    data = json.loads((tmp_path / ".mcp.json").read_text())
    assert "other" in data["mcpServers"]
    assert "lorekeep" in data["mcpServers"]


def test_codex_write_config_replaces_with_following_table(tmp_path: Path):
    """write_config finds the next [table] boundary when replacing lorekeep block."""
    (tmp_path / "config.toml").write_text(
        '[mcp_servers.lorekeep]\ncommand = "old"\n\n'
        '[other_table]\nkey = "value"\n'
    )
    codex.write_config(tmp_path, "uvx", ["lorekeep", "serve"], ns=None)
    result = (tmp_path / "config.toml").read_text()
    assert "old" not in result
    assert "[other_table]" in result
    assert "lorekeep" in result


# ── write_hook tests ─────────────────────────────────────────────────────


def test_cursor_write_hook(tmp_path: Path):
    path = cursor.write_hook(tmp_path, "uvx", ["lorekeep", "hook"])
    assert path.exists()
    data = json.loads(path.read_text())
    assert "sessionEnd" in data["hooks"]


def test_cursor_write_hook_merges_corrupt(tmp_path: Path):
    """Corrupt hooks.json is replaced cleanly."""
    d = tmp_path / ".cursor"
    d.mkdir(parents=True)
    (d / "hooks.json").write_text("not-json{")
    path = cursor.write_hook(tmp_path, "uvx", ["lorekeep", "hook"])
    data = json.loads(path.read_text())
    assert "sessionEnd" in data["hooks"]


def test_codex_write_hook(tmp_path: Path):
    path = codex.write_hook(tmp_path, "uvx", ["lorekeep", "hook"])
    assert path.exists()
    data = json.loads(path.read_text())
    assert "Stop" in data["hooks"]


def test_codex_write_hook_merges_existing(tmp_path: Path):
    """write_hook preserves existing hooks when hooks.json exists."""
    d = tmp_path / ".codex"
    d.mkdir(parents=True)
    existing = {"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "x"}]}]}}
    (d / "hooks.json").write_text(json.dumps(existing))
    codex.write_hook(tmp_path, "uvx", ["lorekeep", "hook"])
    data = json.loads((d / "hooks.json").read_text())
    assert "PreToolUse" in data["hooks"]
    assert "Stop" in data["hooks"]


def test_codex_write_hook_corrupt_existing(tmp_path: Path):
    d = tmp_path / ".codex"
    d.mkdir(parents=True)
    (d / "hooks.json").write_text("corrupt")
    codex.write_hook(tmp_path, "uvx", ["lorekeep", "hook"])
    data = json.loads((d / "hooks.json").read_text())
    assert "Stop" in data["hooks"]


def test_claude_write_hook(tmp_path: Path):
    path = claude_code.write_hook(tmp_path, "uvx", ["lorekeep", "hook"])
    assert path.exists()
    data = json.loads(path.read_text())
    assert "SessionEnd" in data["hooks"]


def test_claude_write_hook_corrupt_settings(tmp_path: Path):
    """Corrupt settings.json is replaced cleanly."""
    d = tmp_path / ".claude"
    d.mkdir(parents=True)
    (d / "settings.json").write_text("not-json")
    claude_code.write_hook(tmp_path, "uvx", ["lorekeep", "hook"])
    data = json.loads((d / "settings.json").read_text())
    assert "SessionEnd" in data["hooks"]
