"""Tests for the grok config.toml writer — env subtable format (issue #255).

The old writer emitted ``env`` as an inline table.  Grok Build re-serializes
config and appends a ``[mcp_servers.lorekeep.env]`` subtable, creating a
duplicate key → TOML parse error.  The fix is to emit env as a subtable from
the start, and to consume any existing subtable when rewriting the block.
"""
from __future__ import annotations

from pathlib import Path

from lorekeep.integrations import grok

CMD = "uvx"
ARGS = ["lorekeep", "serve", "--transport", "stdio"]


def _config_path(isolated_home: Path) -> Path:
    return isolated_home / ".grok" / "config.toml"


class TestEnvSubtableFormat:
    """The block must use a subtable, not inline, for env."""

    def test_env_is_subtable_not_inline(self, isolated_home, tmp_path):
        """No 'env = {' inline table in the written file."""
        path = grok.write_config(tmp_path, CMD, ARGS, "me", scope="user")
        text = path.read_text()
        assert "[mcp_servers.lorekeep.env]" in text
        assert "env = {" not in text

    def test_env_values_in_subtable(self, isolated_home, tmp_path):
        """Env keys are individual lines under the subtable."""
        path = grok.write_config(tmp_path, CMD, ARGS, "backend", scope="user")
        text = path.read_text()
        assert 'LOREKEEP_AGENT = "grok"' in text
        assert 'LOREKEEP_NS = "backend"' in text

    def test_env_without_ns(self, isolated_home, tmp_path):
        """Only LOREKEEP_AGENT when no namespace."""
        path = grok.write_config(tmp_path, CMD, ARGS, None, scope="user")
        text = path.read_text()
        assert 'LOREKEEP_AGENT = "grok"' in text
        assert "LOREKEEP_NS" not in text


class TestRewriteConsumesSubtable:
    """Rewriting must remove any stale [mcp_servers.lorekeep.env] subtable."""

    def test_rewrite_after_grok_reserialize(self, isolated_home, tmp_path):
        """Simulate Grok Build appending an env subtable after lorekeep's block."""
        grok.write_config(tmp_path, CMD, ARGS, "me", scope="user")
        path = _config_path(isolated_home)
        text = path.read_text()

        # Simulate Grok Build adding an inline env + subtable (the bug scenario)
        text = text.replace(
            'LOREKEEP_NS = "me"',
            'LOREKEEP_NS = "me"\n\n[mcp_servers.lorekeep.env]\nLOREKEEP_AGENT = "grok"\nLOREKEEP_NS = "me"',
        )
        path.write_text(text)

        # Rewrite — lorekeep should consume the stale subtable
        grok.write_config(tmp_path, CMD, ARGS, "me", scope="user")
        final = path.read_text()

        # Count env headers — must be exactly 1
        assert final.count("[mcp_servers.lorekeep.env]") == 1
        # No inline env
        assert "env = {" not in final

    def test_rewrite_old_inline_format(self, isolated_home, tmp_path):
        """Old inline-format config gets cleanly replaced with subtable format."""
        path = _config_path(isolated_home)
        path.parent.mkdir(parents=True)
        path.write_text(
            "[mcp_servers.lorekeep]\n"
            'command = "uvx"\n'
            'args = ["lorekeep", "serve"]\n'
            "enabled = true\n"
            'env = { LOREKEEP_AGENT = "grok", LOREKEEP_NS = "me" }\n'
        )

        grok.write_config(tmp_path, CMD, ARGS, "me", scope="user")
        text = path.read_text()
        assert "[mcp_servers.lorekeep.env]" in text
        assert "env = {" not in text
        assert text.count("[mcp_servers.lorekeep]") == 1


class TestNoDuplicateKey:
    """The produced TOML must not declare any key twice."""

    def test_idempotent_rewrite_produces_valid_toml(self, isolated_home, tmp_path):
        """Three rewrites must leave a clean single-declaration file."""
        for _ in range(3):
            grok.write_config(tmp_path, CMD, ARGS, "me", scope="user")

        path = _config_path(isolated_home)
        text = path.read_text()

        # No key declared twice
        assert text.count("[mcp_servers.lorekeep]") == 1
        assert text.count("[mcp_servers.lorekeep.env]") == 1
        assert text.count("command =") == 1
        assert text.count("enabled =") == 1
        assert text.count("LOREKEEP_AGENT") == 1
