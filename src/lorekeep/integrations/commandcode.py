"""Command Code MCP config plus debounced Stop-hook writer.

Command Code uses the standard ``mcpServers`` JSON format with a
``transport: "stdio"`` field on each entry.  Project scope writes
``.commandcode/mcp.json``; user scope writes ``~/.commandcode/mcp.json``.
Command Code has no SessionEnd event, so Stop is coalesced by Lorekeep's daemon
and treated as an approximate end only after the configured idle grace.
"""
from __future__ import annotations

from pathlib import Path

from lorekeep.integrations.common import (
    merge_json_config,
    shell_join,
    upsert_lorekeep_hook,
)


def _commandcode_home() -> Path:
    return Path.home() / ".commandcode"


def config_target(target_dir: Path, scope: str = "project") -> Path:
    if scope == "user":
        return _commandcode_home() / "mcp.json"
    return Path(target_dir) / ".commandcode" / "mcp.json"


def hook_target(target_dir: Path, scope: str = "project") -> Path:
    if scope == "user":
        return _commandcode_home() / "settings.json"
    return Path(target_dir) / ".commandcode" / "settings.json"


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


def write_hook(
    target_dir: Path,
    command: str,
    args: list[str],
    *,
    scope: str = "project",
) -> Path | None:
    cmd = shell_join(command, args)

    def mutate(data: dict) -> None:
        upsert_lorekeep_hook(data, "Stop", {
            "hooks": [{"type": "command", "command": cmd, "timeout": 30}]
        })

    return merge_json_config(
        hook_target(target_dir, scope), mutate, reset_if_corrupt=True,
    )
