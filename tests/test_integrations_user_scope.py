"""User-scope wiring: correct targets, no churn, and no collateral damage.

The user-scope targets are files the agents own and write live. ``~/.claude.json``
in particular is mode 600 and holds the user's OAuth account and machine ID, and
the daemon re-checks wiring on a timer — so "write the right file", "write it
only when it actually changed" and "never destroy what else is in it" are all
load-bearing, not nice-to-haves.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lorekeep.integrations import (
    claude_code, codex, commandcode, copilot, cursor, grok, opencode, qoder,
)
from lorekeep.integrations.registry import all_specs

CMD = "uvx"
ARGS = ["lorekeep", "serve", "--transport", "stdio"]
HOOK_ARGS = ["lorekeep", "hook"]

WRITERS = {
    "claude": claude_code,
    "codex": codex,
    "cursor": cursor,
    "opencode": opencode,
    "grok": grok,
    "qoder": qoder,
    "copilot": copilot,
    "cmd": commandcode,
}


def _expected(spec, scope: str, project: Path, home: Path) -> tuple[Path | None, Path | None]:
    def resolve(declared: str | None) -> Path | None:
        if not declared:
            return None
        if declared.startswith("~/"):
            return home / declared[2:]
        return project / declared

    if scope == "user":
        return resolve(spec.user_config), resolve(spec.user_hook)
    return resolve(spec.project_config) or resolve(spec.user_config), resolve(spec.project_hook)


@pytest.mark.parametrize("spec", all_specs(), ids=lambda s: s.name)
@pytest.mark.parametrize("scope", ["project", "user"])
def test_writer_targets_match_the_registry(spec, scope, isolated_home, tmp_path):
    """The registry declares where; the writer decides how. They must agree."""
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    writer = WRITERS[spec.name]
    want_config, want_hook = _expected(spec, scope, project, isolated_home)

    assert writer.write_config(project, CMD, ARGS, "me", scope=scope) == want_config
    if spec.supports_hook:
        assert writer.write_hook(project, CMD, HOOK_ARGS, scope=scope) == want_hook
    else:
        assert not hasattr(writer, "write_hook")


@pytest.mark.parametrize("spec", all_specs(), ids=lambda s: s.name)
@pytest.mark.parametrize("scope", ["project", "user"])
def test_rewriting_identical_wiring_does_not_touch_the_file(spec, scope, isolated_home, tmp_path):
    """The daemon re-checks on a timer; churning mtime would retrigger watchers."""
    if not spec.supports_hook:
        pytest.skip(f"{spec.name}: no hooks")
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    writer = WRITERS[spec.name]

    config = writer.write_config(project, CMD, ARGS, "me", scope=scope)
    hook = writer.write_hook(project, CMD, HOOK_ARGS, scope=scope)
    before = (config.stat().st_mtime_ns, hook.stat().st_mtime_ns)

    assert writer.write_config(project, CMD, ARGS, "me", scope=scope) is None
    assert writer.write_hook(project, CMD, HOOK_ARGS, scope=scope) is None
    assert (config.stat().st_mtime_ns, hook.stat().st_mtime_ns) == before


@pytest.mark.parametrize("spec", all_specs(), ids=lambda s: s.name)
@pytest.mark.parametrize("scope", ["project", "user"])
def test_changing_namespace_rewrites(spec, scope, isolated_home, tmp_path):
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    writer = WRITERS[spec.name]
    writer.write_config(project, CMD, ARGS, "me", scope=scope)
    assert writer.write_config(project, CMD, ARGS, "teams/backend", scope=scope) is not None


# ── ~/.claude.json is the dangerous one ──────────────────────────────────────

REALISTIC_CLAUDE_JSON = {
    "numStartups": 412,
    "installMethod": "native",
    "oauthAccount": {
        "accountUuid": "11111111-2222-3333-4444-555555555555",
        "emailAddress": "dev@example.test",
        "organizationName": "Example Org",
    },
    "machineID": "abc123def456",
    "projects": {
        "/home/dev/work/thing": {"allowedTools": [], "history": [{"display": "hello"}]},
    },
    "mcpServers": {"other-server": {"command": "foo", "args": ["bar"]}},
}


@pytest.fixture
def claude_json(isolated_home) -> Path:
    path = isolated_home / ".claude.json"
    path.write_text(json.dumps(REALISTIC_CLAUDE_JSON, indent=2), encoding="utf-8")
    path.chmod(0o600)
    return path


def test_user_scope_preserves_credentials_and_history(claude_json, tmp_path):
    written = claude_code.write_config(tmp_path, CMD, ARGS, "me", scope="user")
    assert written == claude_json

    data = json.loads(claude_json.read_text())
    assert data["oauthAccount"] == REALISTIC_CLAUDE_JSON["oauthAccount"]
    assert data["machineID"] == "abc123def456"
    assert data["projects"] == REALISTIC_CLAUDE_JSON["projects"]
    assert data["numStartups"] == 412
    assert "other-server" in data["mcpServers"]
    assert data["mcpServers"]["lorekeep"]["env"]["LOREKEEP_AGENT"] == "claude"


def test_user_scope_preserves_file_mode(claude_json, tmp_path):
    claude_code.write_config(tmp_path, CMD, ARGS, "me", scope="user")
    assert claude_json.stat().st_mode & 0o777 == 0o600


def test_unparseable_config_is_skipped_not_clobbered(isolated_home, tmp_path):
    """Claude writes this file live; a mid-write read must not cost the user data."""
    path = isolated_home / ".claude.json"
    corrupt = '{"oauthAccount": {"emailAddress": "dev@exam'
    path.write_text(corrupt, encoding="utf-8")

    assert claude_code.write_config(tmp_path, CMD, ARGS, "me", scope="user") is None
    assert path.read_text() == corrupt


def test_non_dict_config_is_skipped(isolated_home, tmp_path):
    path = isolated_home / ".claude.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert claude_code.write_config(tmp_path, CMD, ARGS, "me", scope="user") is None
    assert path.read_text() == "[1, 2, 3]"


def test_reformatted_file_with_same_content_is_left_alone(claude_json, tmp_path):
    """Agents pick their own key order and indent; comparing bytes would loop forever."""
    claude_code.write_config(tmp_path, CMD, ARGS, "me", scope="user")
    data = json.loads(claude_json.read_text())
    claude_json.write_text(json.dumps(data, indent=4, sort_keys=True), encoding="utf-8")
    before = claude_json.read_text()

    assert claude_code.write_config(tmp_path, CMD, ARGS, "me", scope="user") is None
    assert claude_json.read_text() == before


def test_no_temp_files_left_behind(claude_json, tmp_path):
    claude_code.write_config(tmp_path, CMD, ARGS, "me", scope="user")
    assert not list(claude_json.parent.glob("*.tmp"))


# ── per-agent user-scope specifics ───────────────────────────────────────────


def test_codex_user_scope_honors_codex_home(isolated_home, tmp_path, monkeypatch):
    elsewhere = tmp_path / "custom-codex"
    monkeypatch.setenv("CODEX_HOME", str(elsewhere))
    assert codex.write_config(tmp_path, CMD, ARGS, "me", scope="user") == elsewhere / "config.toml"
    assert codex.write_hook(tmp_path, CMD, HOOK_ARGS, scope="user") == elsewhere / "hooks.json"


def test_codex_user_scope_preserves_other_tables(isolated_home, tmp_path):
    path = isolated_home / ".codex" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text('[projects."/home/dev/thing"]\ntrust_level = "trusted"\n')
    codex.write_config(tmp_path, CMD, ARGS, "me", scope="user")
    text = path.read_text()
    assert '[projects."/home/dev/thing"]' in text
    assert 'trust_level = "trusted"' in text
    assert "[mcp_servers.lorekeep]" in text


def test_opencode_user_scope_honors_xdg_config_home(isolated_home, tmp_path, monkeypatch):
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    assert opencode.write_config(tmp_path, CMD, ARGS, "me", scope="user") == (
        xdg / "opencode" / "opencode.json"
    )
    assert opencode.write_hook(tmp_path, CMD, HOOK_ARGS, scope="user") == (
        xdg / "opencode" / "plugins" / "lorekeep.ts"
    )


def test_opencode_plugin_lands_in_the_autoloaded_directory(isolated_home, tmp_path):
    """opencode auto-loads scripts under plugins/ — the name is load-bearing."""
    assert opencode.hook_target(tmp_path, "user").parent.name == "plugins"
    assert opencode.hook_target(tmp_path, "project").parent.name == "plugins"


def test_cursor_user_scope_preserves_other_servers(isolated_home, tmp_path):
    path = isolated_home / ".cursor" / "mcp.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"mcpServers": {"other": {"command": "foo"}}}))
    cursor.write_config(tmp_path, CMD, ARGS, "me", scope="user")
    data = json.loads(path.read_text())
    assert "other" in data["mcpServers"]
    assert "lorekeep" in data["mcpServers"]


def test_user_and_project_scope_are_independent(isolated_home, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    claude_code.write_config(project, CMD, ARGS, "me", scope="project")
    assert (project / ".mcp.json").exists()
    assert not (isolated_home / ".claude.json").exists()


# ── GitHub Copilot specifics ─────────────────────────────────────────────────

def test_copilot_user_scope_target(isolated_home):
    assert copilot.config_target(Path("/ignored"), "user") == isolated_home / ".copilot" / "mcp-config.json"


def test_copilot_project_scope_target(tmp_path):
    assert copilot.config_target(tmp_path, "project") == tmp_path / ".github" / "mcp.json"


def test_copilot_write_config_sets_mcp_servers(isolated_home, tmp_path):
    written = copilot.write_config(tmp_path, CMD, ARGS, "me", scope="user")
    assert written == isolated_home / ".copilot" / "mcp-config.json"
    data = json.loads(written.read_text())
    entry = data["mcpServers"]["lorekeep"]
    assert entry["type"] == "local"
    assert entry["command"] == CMD
    assert entry["args"] == ARGS
    assert entry["env"]["LOREKEEP_AGENT"] == "copilot"
    assert entry["env"]["LOREKEEP_NS"] == "me"


def test_copilot_write_preserves_other_servers(isolated_home, tmp_path):
    path = isolated_home / ".copilot" / "mcp-config.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"mcpServers": {"other": {"command": "foo"}}}))
    copilot.write_config(tmp_path, CMD, ARGS, "me", scope="user")
    data = json.loads(path.read_text())
    assert "other" in data["mcpServers"]
    assert "lorekeep" in data["mcpServers"]


def test_copilot_idempotent_rewrite(isolated_home, tmp_path):
    copilot.write_config(tmp_path, CMD, ARGS, "me", scope="user")
    assert copilot.write_config(tmp_path, CMD, ARGS, "me", scope="user") is None


# ── Command Code specifics ───────────────────────────────────────────────────

def test_cmd_user_scope_target(isolated_home):
    assert commandcode.config_target(Path("/ignored"), "user") == isolated_home / ".commandcode" / "mcp.json"


def test_cmd_project_scope_target(tmp_path):
    assert commandcode.config_target(tmp_path, "project") == tmp_path / ".commandcode" / "mcp.json"


def test_cmd_write_config_sets_mcp_servers(isolated_home, tmp_path):
    written = commandcode.write_config(tmp_path, CMD, ARGS, "me", scope="user")
    assert written == isolated_home / ".commandcode" / "mcp.json"
    data = json.loads(written.read_text())
    entry = data["mcpServers"]["lorekeep"]
    assert entry["transport"] == "stdio"
    assert entry["command"] == CMD
    assert entry["args"] == ARGS
    assert entry["env"]["LOREKEEP_AGENT"] == "cmd"
    assert entry["env"]["LOREKEEP_NS"] == "me"


def test_cmd_write_preserves_other_servers(isolated_home, tmp_path):
    path = isolated_home / ".commandcode" / "mcp.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"mcpServers": {"other": {"command": "foo"}}}))
    commandcode.write_config(tmp_path, CMD, ARGS, "me", scope="user")
    data = json.loads(path.read_text())
    assert "other" in data["mcpServers"]
    assert "lorekeep" in data["mcpServers"]


def test_cmd_idempotent_rewrite(isolated_home, tmp_path):
    commandcode.write_config(tmp_path, CMD, ARGS, "me", scope="user")
    assert commandcode.write_config(tmp_path, CMD, ARGS, "me", scope="user") is None
