"""Qoder MCP config (.qoder/mcp.json) writer.

Qoder uses the standard ``mcpServers`` JSON format (same shape as Cursor and
Claude Code's ``.mcp.json``).  Project scope writes ``.qoder/mcp.json``; user
scope writes ``~/.qoder/mcp.json``.  No declarative session-end hooks yet.
"""
from __future__ import annotations

from pathlib import Path

from lorekeep.integrations.common import merge_json_config


def _qoder_home() -> Path:
    return Path.home() / ".qoder"


def config_target(target_dir: Path, scope: str = "project") -> Path:
    if scope == "user":
        return _qoder_home() / "mcp.json"
    return Path(target_dir) / ".qoder" / "mcp.json"


def hook_target(target_dir: Path, scope: str = "project") -> Path | None:
    return None  # Qoder has no declarative session-end hooks yet.


def write_config(
    target_dir: Path,
    command: str,
    args: list[str],
    ns: str | None = None,
    *,
    scope: str = "project",
) -> Path | None:
    env: dict[str, str] = {"LOREKEEP_AGENT": "qoder"}
    if ns:
        env["LOREKEEP_NS"] = ns

    entry: dict = {
        "command": command,
        "args": args,
        "env": env,
    }

    def mutate(data: dict) -> None:
        data.setdefault("mcpServers", {})["lorekeep"] = entry

    return merge_json_config(
        config_target(target_dir, scope), mutate, reset_if_corrupt=True,
    )
