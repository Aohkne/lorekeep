"""GitHub Copilot CLI MCP config plus local SessionEnd hook writer.

GitHub Copilot CLI uses the ``mcpServers`` JSON format with a ``type: "local"``
field on each entry. Lorekeep installs its local-ingest hook only at user scope;
project hooks also execute in ephemeral Copilot cloud agents where the local
Lorekeep data home and interpreter do not exist.
"""
from __future__ import annotations

import os
from pathlib import Path

from lorekeep.integrations.common import (
    merge_json_config,
    shell_join,
    upsert_lorekeep_hook,
)


def _copilot_home() -> Path:
    return Path(os.environ.get("COPILOT_HOME", Path.home() / ".copilot"))


def config_target(target_dir: Path, scope: str = "project") -> Path:
    if scope == "user":
        return _copilot_home() / "mcp-config.json"
    return Path(target_dir) / ".github" / "mcp.json"


def hook_target(target_dir: Path, scope: str = "project") -> Path | None:
    if scope != "user":
        return None
    return _copilot_home() / "hooks" / "lorekeep.json"


def write_config(
    target_dir: Path,
    command: str,
    args: list[str],
    ns: str | None = None,
    *,
    scope: str = "project",
) -> Path | None:
    env: dict[str, str] = {"LOREKEEP_AGENT": "copilot"}
    if ns:
        env["LOREKEEP_READ_NS"] = ns

    entry: dict = {
        "type": "local",
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
    path = hook_target(target_dir, scope)
    if path is None:
        return None
    cmd = shell_join(command, args)

    def mutate(data: dict) -> None:
        data["version"] = 1
        upsert_lorekeep_hook(data, "sessionEnd", {
            "type": "command", "command": cmd, "timeoutSec": 5,
        })

    return merge_json_config(path, mutate, reset_if_corrupt=True)
