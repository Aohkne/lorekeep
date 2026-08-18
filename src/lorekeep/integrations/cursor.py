"""Cursor MCP config + sessionEnd hook writer.

Project scope writes under ``.cursor/``; user scope writes under ``~/.cursor/``.
"""
from __future__ import annotations

from pathlib import Path

from lorekeep.integrations.common import (
    merge_json_config,
    shell_join,
    upsert_lorekeep_hook,
)


def _cursor_dir(target_dir: Path, scope: str) -> Path:
    if scope == "user":
        return Path("~/.cursor").expanduser()
    return Path(target_dir) / ".cursor"


def config_target(target_dir: Path, scope: str = "project") -> Path:
    return _cursor_dir(target_dir, scope) / "mcp.json"


def hook_target(target_dir: Path, scope: str = "project") -> Path | None:
    return _cursor_dir(target_dir, scope) / "hooks.json"


def write_config(
    target_dir: Path,
    command: str,
    args: list[str],
    ns: str | None = None,
    *,
    scope: str = "project",
) -> Path | None:
    entry = {"command": command, "args": args, "env": {"LOREKEEP_AGENT": "cursor"}}
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
    """Write a sessionEnd hook to hooks.json.

    Cursor's hook format uses a single command string (shell form).
    """
    path = hook_target(target_dir, scope)
    if path is None:
        return None
    cmd_str = shell_join(command, args)

    def mutate(data: dict) -> None:
        data["version"] = data.get("version", 1)
        upsert_lorekeep_hook(
            data, "sessionEnd", {"command": cmd_str, "timeout": 30}
        )

    return merge_json_config(
        path, mutate, reset_if_corrupt=True,
    )
