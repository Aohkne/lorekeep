"""Grok Build MCP config (~/.grok/config.toml) writer.

Grok Build uses a Codex-style TOML config with ``[mcp_servers.<name>]`` tables.
There is no project-scope config today — only user-scope (``~/.grok/config.toml``).
Project-scope falls back to the same user file so ``lorekeep mcp add --agent grok``
always works.

The TOML is edited by hand (not round-tripped through a parser) for the same
reason as the Codex writer: Grok Build writes its own tables into the same file,
and a reformatting round-trip would rewrite all of them.
"""
from __future__ import annotations

import os
from pathlib import Path

from lorekeep.integrations.common import atomic_write

_HEADER = "[mcp_servers.lorekeep]"
_ENV_HEADER = "[mcp_servers.lorekeep.env]"


def _grok_home() -> Path:
    return Path(os.environ.get("GROK_HOME", Path.home() / ".grok"))


def config_target(target_dir: Path, scope: str = "project") -> Path:
    return _grok_home() / "config.toml"


def hook_target(target_dir: Path, scope: str = "project") -> Path | None:
    return None  # Grok Build has no declarative session-end hooks yet.


def _toml_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _toml_quote_list(items: list[str]) -> str:
    return "[" + ", ".join(f'"{_toml_escape(i)}"' for i in items) + "]"


def _lorekeep_block(command: str, args: list[str], ns: str | None) -> str:
    env_lines = ['LOREKEEP_AGENT = "grok"']
    if ns:
        env_lines.append(f'LOREKEEP_NS = "{_toml_escape(ns)}"')
    lines = [
        _HEADER,
        f'command = "{_toml_escape(command)}"',
        f"args = {_toml_quote_list(args)}",
        "enabled = true",
        "",
        _ENV_HEADER,
        *env_lines,
    ]
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
            line = lines[i]
            # Skip subtables that belong to the lorekeep block
            # (e.g. [mcp_servers.lorekeep.env]) so they get replaced too.
            if line.startswith("[") and not line.startswith("[mcp_servers.lorekeep"):
                end = i
                break
        before = lines[:header_idx]
        after = lines[end:]
        rebuilt = before + [block] + ([""] + after if after else [])
        new_text = "\n".join(rebuilt) + "\n"
    if new_text == text:
        return None
    return atomic_write(path, new_text)
