import json
from pathlib import Path
from laputa.integrations.common import resolve_command, agent_memory_snippet
from laputa.integrations import claude_code, cursor, codex


def test_resolve_command_pypi():
    assert resolve_command(None) == ("uvx", ["laputa", "serve", "--transport", "stdio"])
    assert resolve_command("pypi") == ("uvx", ["laputa", "serve", "--transport", "stdio"])


def test_resolve_command_local():
    assert resolve_command("local") == ("laputa", ["serve", "--transport", "stdio"])


def test_resolve_command_git():
    cmd, args = resolve_command("git+https://github.com/x/laputa.git")
    assert cmd == "uvx"
    assert args[:2] == ["--from", "git+https://github.com/x/laputa.git"]
    assert "serve" in args


def test_claude_writes_mcp_json(tmp_path: Path):
    claude_code.write_config(tmp_path, "uvx", ["laputa", "serve", "--transport", "stdio"],
                             ns="teams/backend")
    data = json.loads((tmp_path / ".mcp.json").read_text())
    assert data["mcpServers"]["laputa"]["command"] == "uvx"
    assert data["mcpServers"]["laputa"]["env"]["LAPUTA_NS"] == "teams/backend"


def test_cursor_writes_mcp_json(tmp_path: Path):
    cursor.write_config(tmp_path, "uvx", ["laputa", "serve", "--transport", "stdio"], ns=None)
    data = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    assert "laputa" in data["mcpServers"]


def test_codex_writes_toml(tmp_path: Path):
    codex.write_config(tmp_path, "uvx", ["laputa", "serve", "--transport", "stdio"],
                       ns="teams/backend")
    text = (tmp_path / "config.toml").read_text()
    assert "[mcp_servers.laputa]" in text
    assert 'command = "uvx"' in text
    assert 'LAPUTA_NS = "teams/backend"' in text


def test_agent_memory_snippet_mentions_provenance():
    s = agent_memory_snippet()
    assert "src" in s and "namespace" in s.lower()
