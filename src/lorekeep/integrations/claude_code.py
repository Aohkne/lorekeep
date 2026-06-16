"""Claude Code MCP config writer (.mcp.json)."""
from __future__ import annotations

import json
from pathlib import Path


def write_config(target_dir: Path, command: str, args: list[str], ns: str | None) -> Path:
    entry = {"command": command, "args": args}
    if ns:
        entry["env"] = {"LOREKEEP_NS": ns}
    path = Path(target_dir) / ".mcp.json"
    existing = {}
    if path.exists():
        existing = json.loads(path.read_text())
    servers = existing.get("mcpServers", {})
    servers["lorekeep"] = entry
    path.write_text(json.dumps({"mcpServers": servers}, indent=2))
    return path
