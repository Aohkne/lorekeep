"""Codex MCP config (config.toml) + Stop hook writer (hooks.json).

Project scope writes ``config.toml`` and ``.codex/hooks.json``; user scope
writes into ``$CODEX_HOME`` (default ``~/.codex``).

The TOML is edited by hand rather than round-tripped through a parser: Codex
writes its own tables (``[projects."…"]``, ``[hooks.state."…"]``) into the same
file, and a reformatting round-trip would rewrite all of them.
"""
from __future__ import annotations

import os
from pathlib import Path

from lorekeep.integrations.common import atomic_write, merge_json_config

_HEADER = "[mcp_servers.lorekeep]"


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))


def config_target(target_dir: Path, scope: str = "project") -> Path:
    if scope == "user":
        return _codex_home() / "config.toml"
    return Path(target_dir) / "config.toml"


def hook_target(target_dir: Path, scope: str = "project") -> Path:
    if scope == "user":
        return _codex_home() / "hooks.json"
    return Path(target_dir) / ".codex" / "hooks.json"


def _toml_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _toml_quote_list(items: list[str]) -> str:
    return "[" + ", ".join(f'"{_toml_escape(i)}"' for i in items) + "]"


def _lorekeep_block(command: str, args: list[str], ns: str | None) -> str:
    lines = [
        _HEADER,
        f'command = "{_toml_escape(command)}"',
        f"args = {_toml_quote_list(args)}",
    ]
    env = ['LOREKEEP_AGENT = "codex"']
    if ns:
        env.append(f'LOREKEEP_NS = "{_toml_escape(ns)}"')
    lines.append("env = { " + ", ".join(env) + " }")
    return "\n".join(lines)


def write_config(
    target_dir: Path,
    command: str,
    args: list[str],
    ns: str | None = None,
    *,
    scope: str = "project",
) -> Path | None:
    if ns and ("\n" in ns or "\r" in ns):
        raise ValueError("namespace must not contain newlines")
    path = config_target(target_dir, scope)
    block = _lorekeep_block(command, args, ns)
    text = path.read_text() if path.exists() else ""
    lines = text.splitlines()
    header_idx = next((i for i, l in enumerate(lines) if l.strip() == _HEADER), -1)
    if header_idx == -1:
        sep = "\n\n" if text.strip() else ""
        new_text = text + sep + block + "\n"
    else:
        end = len(lines)
        for i in range(header_idx + 1, len(lines)):
            if lines[i].startswith("["):   # next top-level table
                end = i
                break
        before = lines[:header_idx]
        after = lines[end:]
        rebuilt = before + [block] + ([""] + after if after else [])
        new_text = "\n".join(rebuilt) + "\n"
    if new_text == text:
        return None
    return atomic_write(path, new_text)


def write_hook(
    target_dir: Path,
    command: str,
    args: list[str],
    *,
    scope: str = "project",
) -> Path | None:
    """Write a Stop hook to hooks.json.

    Codex fires Stop after every turn. The lorekeep hook command is
    idempotent (manifest dedup) — zero cost if memories unchanged.
    """
    cmd_str = " ".join([command, *args])

    def mutate(data: dict) -> None:
        data.setdefault("hooks", {})["Stop"] = [{
            "hooks": [{
                "type": "command",
                "command": cmd_str,
                "timeout": 30,
            }]
        }]

    return merge_json_config(
        hook_target(target_dir, scope), mutate, reset_if_corrupt=True,
    )
