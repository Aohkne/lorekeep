"""Codex MCP config writer (config.toml [mcp_servers.laputa])."""
from __future__ import annotations

from pathlib import Path


def _toml_quote_list(items: list[str]) -> str:
    return "[" + ", ".join(f'"{i}"' for i in items) + "]"


def write_config(target_dir: Path, command: str, args: list[str], ns: str | None) -> Path:
    lines = [
        "[mcp_servers.laputa]",
        f'command = "{command}"',
        f"args = {_toml_quote_list(args)}",
    ]
    if ns:
        lines.append(f'env = {{ LAPUTA_NS = "{ns}" }}')
    path = Path(target_dir) / "config.toml"
    sep = "\n\n" if path.exists() else ""
    with path.open("a") as f:
        f.write(sep + "\n".join(lines) + "\n")
    return path
