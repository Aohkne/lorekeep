---
name: lorekeep-design
description: "Key architecture decisions for Lorekeep"
metadata:
  node_type: memory
  type: design
---

Lorekeep uses networkx for the in-memory GraphStore.
The MCP server uses FastMCP with stdio transport.
Facts are stored in deterministic JSONL format for git-friendly syncing.
