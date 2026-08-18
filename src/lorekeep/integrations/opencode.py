"""opencode MCP config (opencode.json) + session.idle plugin writer.

Project scope writes ``opencode.json`` and ``.opencode/plugins/lorekeep.ts``;
user scope writes into ``$XDG_CONFIG_HOME/opencode`` (default ``~/.config/opencode``).
opencode auto-loads every script under ``plugins/``, so the file alone is
enough — the ``plugin`` array in opencode.json is for npm packages.
"""
from __future__ import annotations

import os
from pathlib import Path

from lorekeep.integrations.common import (
    merge_json_config,
    shell_join,
    write_text_if_changed,
)


def _opencode_config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "opencode"


def config_target(target_dir: Path, scope: str = "project") -> Path:
    if scope == "user":
        return _opencode_config_dir() / "opencode.json"
    return Path(target_dir) / "opencode.json"


def hook_target(target_dir: Path, scope: str = "project") -> Path:
    if scope == "user":
        return _opencode_config_dir() / "plugins" / "lorekeep.ts"
    return Path(target_dir) / ".opencode" / "plugins" / "lorekeep.ts"


def write_config(
    target_dir: Path,
    command: str,
    args: list[str],
    ns: str | None = None,
    *,
    scope: str = "project",
) -> Path | None:
    entry: dict = {
        "type": "local",
        "command": [command, *args],
        "enabled": True,
        "environment": {"LOREKEEP_AGENT": "opencode"},
    }
    if ns:
        entry["environment"]["LOREKEEP_READ_NS"] = ns

    def mutate(data: dict) -> None:
        data.setdefault("mcp", {})["lorekeep"] = entry

    return merge_json_config(
        config_target(target_dir, scope), mutate, reset_if_corrupt=True,
    )


_PLUGIN_TS = """\
import type {{ Plugin }} from "@opencode-ai/plugin"

export default {{
  event: async ({{ $, event }}) => {{
    if (event.type === "session.idle") {{
      const properties = (event as any).properties ?? {{}}
      const sessionID = properties.sessionID ?? properties.id ?? ""
      await $`{cmd} --session-id ${{sessionID}} --cwd ${{process.cwd()}}`
    }}
  }},
}} satisfies Plugin
"""


def write_hook(
    target_dir: Path,
    command: str,
    args: list[str],
    *,
    scope: str = "project",
) -> Path | None:
    """Write a session.idle plugin to plugins/lorekeep.ts.

    opencode has no declarative hooks — this TS plugin subscribes to
    session.idle and runs the lorekeep hook command.
    """
    cmd = shell_join(command, args)
    path = hook_target(target_dir, scope)
    return write_text_if_changed(path, _PLUGIN_TS.format(cmd=cmd))
