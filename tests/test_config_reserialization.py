"""Config robustness tests for all coding-agent integrations.

Verifies that every agent's config writer:
1. Produces parseable output (no duplicate keys for TOML agents)
2. Survives the agent re-serializing its config file
3. Is idempotent (rewriting an already-correct file is a no-op)
4. Migrates from old formats cleanly

The TOML agents (Codex, Grok) are the primary concern — issue #255 showed
that inline env tables collide with subtable re-serialization. The JSON
agents (Claude Code, Cursor, opencode, Qoder) are safe by construction
but get regression tests for confidence.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lorekeep.integrations import claude_code, codex, cursor, grok, opencode, qoder

CMD = "uvx"
ARGS = ["lorekeep", "serve", "--transport", "stdio"]


# ── TOML agents: Codex + Grok ──────────────────────────────────────────────


class TestCodexEnvSubtable:
    """Codex must emit env as a subtable (same fix as Grok)."""

    def test_env_is_subtable_not_inline(self, isolated_home, tmp_path):
        path = codex.write_config(tmp_path, CMD, ARGS, "me", scope="user")
        text = path.read_text()
        assert "[mcp_servers.lorekeep.env]" in text
        assert "env = {" not in text

    def test_env_values_correct(self, isolated_home, tmp_path):
        path = codex.write_config(tmp_path, CMD, ARGS, "backend", scope="user")
        text = path.read_text()
        assert 'LOREKEEP_AGENT = "codex"' in text
        assert 'LOREKEEP_READ_NS = "backend"' in text

    def test_env_without_ns(self, isolated_home, tmp_path):
        path = codex.write_config(tmp_path, CMD, ARGS, None, scope="user")
        text = path.read_text()
        assert 'LOREKEEP_AGENT = "codex"' in text
        assert "LOREKEEP_READ_NS" not in text

    def test_rewrite_consumes_subtable(self, isolated_home, tmp_path):
        """Rewriting must remove stale subtable from re-serialization."""
        codex.write_config(tmp_path, CMD, ARGS, "me", scope="user")
        path = isolated_home / ".codex" / "config.toml"
        text = path.read_text()

        # Simulate re-serialization adding a duplicate env subtable
        text += '\n[mcp_servers.lorekeep.env]\nLOREKEEP_AGENT = "codex"\nLOREKEEP_NS = "me"'
        path.write_text(text)

        codex.write_config(tmp_path, CMD, ARGS, "me", scope="user")
        final = path.read_text()
        assert final.count("[mcp_servers.lorekeep.env]") == 1
        assert "env = {" not in final
        assert "LOREKEEP_NS" not in final
        assert 'LOREKEEP_READ_NS = "me"' in final

    def test_migrates_old_inline_format(self, isolated_home, tmp_path):
        """Old inline-format config gets cleanly replaced with subtable."""
        path = isolated_home / ".codex" / "config.toml"
        path.parent.mkdir(parents=True)
        path.write_text(
            "[mcp_servers.lorekeep]\n"
            'command = "uvx"\n'
            'args = ["lorekeep", "serve"]\n'
            'env = { LOREKEEP_AGENT = "codex", LOREKEEP_NS = "me" }\n'
        )
        codex.write_config(tmp_path, CMD, ARGS, "me", scope="user")
        text = path.read_text()
        assert "[mcp_servers.lorekeep.env]" in text
        assert "env = {" not in text
        assert text.count("[mcp_servers.lorekeep]") == 1

    def test_preserves_other_tables(self, isolated_home, tmp_path):
        """Other Codex tables must survive lorekeep rewriting its block."""
        path = isolated_home / ".codex" / "config.toml"
        path.parent.mkdir(parents=True)
        path.write_text('[projects."/home/dev/thing"]\ntrust_level = "trusted"\n')
        codex.write_config(tmp_path, CMD, ARGS, "me", scope="user")
        text = path.read_text()
        assert '[projects."/home/dev/thing"]' in text
        assert 'trust_level = "trusted"' in text

    def test_idempotent_rewrite(self, isolated_home, tmp_path):
        """Three rewrites must leave a clean single-declaration file."""
        for _ in range(3):
            codex.write_config(tmp_path, CMD, ARGS, "me", scope="user")
        path = isolated_home / ".codex" / "config.toml"
        text = path.read_text()
        assert text.count("[mcp_servers.lorekeep]") == 1
        assert text.count("[mcp_servers.lorekeep.env]") == 1
        assert text.count("LOREKEEP_AGENT") == 1


class TestGrokEnvSubtable:
    """Grok already fixed — these are regression tests."""

    def test_env_is_subtable_not_inline(self, isolated_home, tmp_path):
        path = grok.write_config(tmp_path, CMD, ARGS, "me", scope="user")
        text = path.read_text()
        assert "[mcp_servers.lorekeep.env]" in text
        assert "env = {" not in text

    def test_rewrite_consumes_subtable(self, isolated_home, tmp_path):
        grok.write_config(tmp_path, CMD, ARGS, "me", scope="user")
        path = isolated_home / ".grok" / "config.toml"
        text = path.read_text()
        text += '\n[mcp_servers.lorekeep.env]\nLOREKEEP_AGENT = "grok"\nLOREKEEP_NS = "me"'
        path.write_text(text)

        grok.write_config(tmp_path, CMD, ARGS, "me", scope="user")
        final = path.read_text()
        assert final.count("[mcp_servers.lorekeep.env]") == 1
        assert "env = {" not in final

    def test_idempotent_rewrite(self, isolated_home, tmp_path):
        for _ in range(3):
            grok.write_config(tmp_path, CMD, ARGS, "me", scope="user")
        path = isolated_home / ".grok" / "config.toml"
        text = path.read_text()
        assert text.count("[mcp_servers.lorekeep]") == 1
        assert text.count("[mcp_servers.lorekeep.env]") == 1
        assert text.count("LOREKEEP_AGENT") == 1


# ── JSON agents: safe by construction, but regression-test anyway ──────────


@pytest.mark.parametrize(
    "name,writer,config_path_segments,env_key",
    [
        ("claude", claude_code, [".claude.json"], "env"),
        ("cursor", cursor, [".cursor", "mcp.json"], "env"),
        ("opencode", opencode, [".config", "opencode", "opencode.json"], "environment"),
        ("qoder", qoder, [".qoder", "mcp.json"], "env"),
    ],
    ids=["claude", "cursor", "opencode", "qoder"],
)
class TestJsonAgentConfigRobustness:

    def test_env_values_present(self, name, writer, config_path_segments, env_key,
                                 isolated_home, tmp_path):
        """The lorekeep entry must carry LOREKEEP_AGENT and namespace."""
        writer.write_config(tmp_path, CMD, ARGS, "backend", scope="user")
        path = isolated_home.joinpath(*config_path_segments)
        data = json.loads(path.read_text())

        servers = data.get("mcpServers") or data.get("mcp", {})
        entry = servers["lorekeep"]
        env = entry[env_key]
        assert env["LOREKEEP_AGENT"] == name
        assert env["LOREKEEP_READ_NS"] == "backend"

    def test_agent_reserialize_survives(self, name, writer, config_path_segments, env_key,
                                         isolated_home, tmp_path):
        """Agent reformats the JSON → lorekeep rewrites → no data lost."""
        writer.write_config(tmp_path, CMD, ARGS, "me", scope="user")
        path = isolated_home.joinpath(*config_path_segments)

        # Simulate agent re-serializing with different formatting + extra keys
        data = json.loads(path.read_text())
        data["agentExtra"] = "preserved"
        servers_key = "mcp" if "mcp" in data else "mcpServers"
        data[servers_key]["other"] = {"command": "foo"}
        path.write_text(json.dumps(data, indent=4, sort_keys=True))

        # Lorekeep rewrite — must preserve agent's keys and not duplicate
        writer.write_config(tmp_path, CMD, ARGS, "me", scope="user")
        final = json.loads(path.read_text())
        assert final["agentExtra"] == "preserved"
        servers = final.get("mcpServers") or final.get("mcp", {})
        assert "other" in servers

        assert servers["lorekeep"][env_key]["LOREKEEP_AGENT"] == name

    def test_idempotent_rewrite(self, name, writer, config_path_segments, env_key,
                                isolated_home, tmp_path):
        """Rewriting an already-correct file returns None (no mtime churn)."""
        writer.write_config(tmp_path, CMD, ARGS, "me", scope="user")
        result = writer.write_config(tmp_path, CMD, ARGS, "me", scope="user")
        assert result is None

    def test_changing_ns_rewrites(self, name, writer, config_path_segments, env_key,
                                   isolated_home, tmp_path):
        """Changing namespace must update the env."""
        writer.write_config(tmp_path, CMD, ARGS, "me", scope="user")
        result = writer.write_config(tmp_path, CMD, ARGS, "backend", scope="user")
        assert result is not None
        path = isolated_home.joinpath(*config_path_segments)
        data = json.loads(path.read_text())
        servers = data.get("mcpServers") or data.get("mcp", {})
        assert servers["lorekeep"][env_key]["LOREKEEP_READ_NS"] == "backend"
