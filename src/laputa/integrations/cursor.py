"""Cursor MCP config writer (.cursor/mcp.json)."""
from __future__ import annotations

import json
from pathlib import Path


def write_config(target_dir: Path, command: str, args: list[str], ns: str | None) -> Path:
    entry = {"command": command, "args": args}
    if ns:
        entry["env"] = {"LAPUTA_NS": ns}
    d = Path(target_dir) / ".cursor"
    d.mkdir(parents=True, exist_ok=True)
    path = d / "mcp.json"
    existing = {}
    if path.exists():
        existing = json.loads(path.read_text())
    servers = existing.get("mcpServers", {})
    servers["laputa"] = entry
    path.write_text(json.dumps({"mcpServers": servers}, indent=2))
    return path
