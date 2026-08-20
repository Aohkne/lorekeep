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

// A plugin is a function, not a hooks object: opencode passes the shell tag
// ``$`` to the outer function and only ``{{ event }}`` to the event hook, and
// the loader rejects non-function exports outright.
export const LorekeepPlugin: Plugin = async ({{ $ }}) => ({{
  event: async ({{ event }}) => {{
    if (event.type === "session.idle") {{
      const sessionID = (event as any).properties?.sessionID ?? ""
      // {cmd} is substituted at wiring time; Bun's shell parses the literal
      // text as command + args. Keep it a literal, never a JS interpolation:
      // interpolated values become one single argv entry.
      await $`{cmd} --session-id ${{sessionID}} --cwd ${{process.cwd()}}`
    }}
  }},
}})
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
    session.idle and runs the lorekeep hook command. It must be a plugin
    *function* that closes over ``$`` (Bun's shell tag): the event hook
    itself receives only ``{ event }``.
    """
    cmd = shell_join(command, args)
    path = hook_target(target_dir, scope)
    return write_text_if_changed(path, _PLUGIN_TS.format(cmd=cmd))
