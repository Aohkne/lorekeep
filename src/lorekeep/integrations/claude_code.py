"""Claude Code MCP config + SessionEnd hook writer.

Project scope writes ``.mcp.json`` and ``.claude/settings.json``; user scope
writes ``~/.claude.json`` and ``~/.claude/settings.json``.
"""
from __future__ import annotations

import os
from pathlib import Path

from lorekeep.integrations.common import merge_json_config, upsert_lorekeep_hook


def _claude_config_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))


def config_target(target_dir: Path, scope: str = "project") -> Path:
    if scope == "user":
        if os.environ.get("CLAUDE_CONFIG_DIR"):
            return _claude_config_dir() / ".claude.json"
        return Path("~/.claude.json").expanduser()
    return Path(target_dir) / ".mcp.json"


def hook_target(target_dir: Path, scope: str = "project") -> Path:
    if scope == "user":
        return _claude_config_dir() / "settings.json"
    return Path(target_dir) / ".claude" / "settings.json"


def write_config(
    target_dir: Path,
    command: str,
    args: list[str],
    ns: str | None = None,
    *,
    scope: str = "project",
) -> Path | None:
    entry = {"command": command, "args": args, "env": {"LOREKEEP_AGENT": "claude"}}
    if ns:
        entry["env"]["LOREKEEP_READ_NS"] = ns

    def mutate(data: dict) -> None:
        data.setdefault("mcpServers", {})["lorekeep"] = entry

    return merge_json_config(config_target(target_dir, scope), mutate)


def write_hook(
    target_dir: Path,
    command: str,
    args: list[str],
    *,
    scope: str = "project",
) -> Path | None:
    """Write a SessionEnd hook to settings.json.

    The command only enqueues Claude's transcript metadata. The daemon performs
    the targeted import and compile outside Claude's hook timeout.
    """
    def mutate(data: dict) -> None:
        upsert_lorekeep_hook(data, "SessionEnd", {
            "hooks": [{
                "type": "command",
                "command": command,
                "args": args,
                "timeout": 30,
            }]
        })

    return merge_json_config(
        hook_target(target_dir, scope), mutate, reset_if_corrupt=True,
    )
