"""GitHub Copilot MCP config (.copilot/mcp-config.json) writer.

GitHub Copilot CLI uses the ``mcpServers`` JSON format with a ``type: "local"``
field on each entry.  Project scope writes ``.github/mcp.json``; user scope
writes ``~/.copilot/mcp-config.json``.  No declarative session-end hooks yet.
"""
from __future__ import annotations

from pathlib import Path

from lorekeep.integrations.common import merge_json_config


def _copilot_home() -> Path:
    return Path.home() / ".copilot"


def config_target(target_dir: Path, scope: str = "project") -> Path:
    if scope == "user":
        return _copilot_home() / "mcp-config.json"
    return Path(target_dir) / ".github" / "mcp.json"


def hook_target(target_dir: Path, scope: str = "project") -> Path | None:
    return None  # Copilot has no declarative session-end hooks yet.


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
