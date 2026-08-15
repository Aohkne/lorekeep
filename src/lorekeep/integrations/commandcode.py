"""Command Code MCP config (.commandcode/mcp.json) writer.

Command Code uses the standard ``mcpServers`` JSON format with a
``transport: "stdio"`` field on each entry.  Project scope writes
``.commandcode/mcp.json``; user scope writes ``~/.commandcode/mcp.json``.
No declarative session-end hooks yet.
"""
from __future__ import annotations

from pathlib import Path

from lorekeep.integrations.common import merge_json_config


def _commandcode_home() -> Path:
    return Path.home() / ".commandcode"


def config_target(target_dir: Path, scope: str = "project") -> Path:
    if scope == "user":
        return _commandcode_home() / "mcp.json"
    return Path(target_dir) / ".commandcode" / "mcp.json"


def hook_target(target_dir: Path, scope: str = "project") -> Path | None:
    return None  # Command Code has no declarative session-end hooks yet.


def write_config(
    target_dir: Path,
    command: str,
    args: list[str],
    ns: str | None = None,
    *,
    scope: str = "project",
) -> Path | None:
    env: dict[str, str] = {"LOREKEEP_AGENT": "cmd"}
    if ns:
        env["LOREKEEP_READ_NS"] = ns

    entry: dict = {
        "transport": "stdio",
        "command": command,
        "args": args,
        "env": env,
    }

    def mutate(data: dict) -> None:
        data.setdefault("mcpServers", {})["lorekeep"] = entry

    return merge_json_config(
        config_target(target_dir, scope), mutate, reset_if_corrupt=True,
    )
