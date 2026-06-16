"""Codex MCP config writer (config.toml [mcp_servers.laputa]).

Idempotent: re-running replaces the existing [mcp_servers.laputa] block instead
of appending a duplicate. Values are escaped so a stray quote/backslash in the
namespace or command can't break the generated TOML.
"""
from __future__ import annotations

from pathlib import Path

_HEADER = "[mcp_servers.laputa]"


def _toml_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _toml_quote_list(items: list[str]) -> str:
    return "[" + ", ".join(f'"{_toml_escape(i)}"' for i in items) + "]"


def _laputa_block(command: str, args: list[str], ns: str | None) -> str:
    lines = [
        _HEADER,
        f'command = "{_toml_escape(command)}"',
        f"args = {_toml_quote_list(args)}",
    ]
    if ns:
        lines.append(f'env = {{ LAPUTA_NS = "{_toml_escape(ns)}" }}')
    return "\n".join(lines)


def write_config(target_dir: Path, command: str, args: list[str], ns: str | None) -> Path:
    if ns and ("\n" in ns or "\r" in ns):
        raise ValueError("namespace must not contain newlines")
    path = Path(target_dir) / "config.toml"
    block = _laputa_block(command, args, ns)
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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text)
    return path
