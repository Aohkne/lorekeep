"""Claude Code MCP config + SessionEnd hook writer.

Project scope writes ``.mcp.json`` and ``.claude/settings.json``; user scope
writes ``~/.claude.json`` and ``~/.claude/settings.json``.
"""
from __future__ import annotations

from pathlib import Path

from lorekeep.integrations.common import merge_json_config


def config_target(target_dir: Path, scope: str = "project") -> Path:
    if scope == "user":
        return Path("~/.claude.json").expanduser()
    return Path(target_dir) / ".mcp.json"


def hook_target(target_dir: Path, scope: str = "project") -> Path:
    if scope == "user":
        return Path("~/.claude/settings.json").expanduser()
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
        entry["env"]["LOREKEEP_NS"] = ns

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

    The hook calls ``lorekeep hook`` which quick-imports Claude memory
    files into raw/ on session end. The daemon (if running) picks up the
    raw/ change and compiles automatically.
    """
    def mutate(data: dict) -> None:
        data.setdefault("hooks", {})["SessionEnd"] = [{
            "hooks": [{
                "type": "command",
                "command": command,
                "args": args,
                "timeout": 30,
            }]
        }]

    return merge_json_config(
        hook_target(target_dir, scope), mutate, reset_if_corrupt=True,
    )
