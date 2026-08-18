"""Qoder MCP config plus native SessionEnd hook writer.

Project MCP scope is ``.mcp.json``; user MCP and hooks share
``~/.qoder/settings.json``. Project hooks live in ``.qoder/settings.json``.
"""
from __future__ import annotations

import os
from pathlib import Path

from lorekeep.integrations.common import (
    merge_json_config,
    upsert_lorekeep_hook,
)


def _qoder_home() -> Path:
    return Path(os.environ.get("QODER_CONFIG_DIR", Path.home() / ".qoder"))


def config_target(target_dir: Path, scope: str = "project") -> Path:
    if scope == "user":
        return _qoder_home() / "settings.json"
    return Path(target_dir) / ".mcp.json"


def hook_target(target_dir: Path, scope: str = "project") -> Path:
    if scope == "user":
        return _qoder_home() / "settings.json"
    return Path(target_dir) / ".qoder" / "settings.json"


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
        env["LOREKEEP_READ_NS"] = ns

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


def write_hook(
    target_dir: Path,
    command: str,
    args: list[str],
    *,
    scope: str = "project",
) -> Path | None:
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
