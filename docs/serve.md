# Serving the knowledge graph to coding agents

## 1. Compile first

```bash
LAPUTA_PROVIDER=fake uv run laputa compile   # produces graph/facts.jsonl
```

## 2. Scope your namespace

Set `LAPUTA_NS` to the namespaces the agent may read (comma-separated). Unset
defaults to `public` (only public facts).

```bash
export LAPUTA_NS=teams/backend
```

## 3. Wire up a coding agent

```bash
uv run laputa mcp add --agent claude --ns teams/backend
uv run laputa mcp add --agent cursor
uv run laputa mcp add --agent codex  --ns teams/backend
```

This writes the agent's MCP config (`.mcp.json` / `.cursor/mcp.json` /
`config.toml`) and prints an agent-memory snippet to paste into `CLAUDE.md` /
`.cursorrules` / `AGENTS.md`. Set `install_source` in `.laputa/config.yaml`
(`pypi` | `local` | `git+URL`) so the emitted command is correct.

### Resulting configs

Claude Code (`.mcp.json`):
```json
{"mcpServers": {"laputa": {"command": "uvx",
  "args": ["laputa", "serve", "--transport", "stdio"],
  "env": {"LAPUTA_NS": "teams/backend"}}}}
```

Codex (`~/.codex/config.toml`):
```toml
[mcp_servers.laputa]
command = "uvx"
args = ["laputa", "serve", "--transport", "stdio"]
env = { LAPUTA_NS = "teams/backend" }
```

## 4. Verify

```bash
uv run laputa doctor
```

Checks the graph loads, schema is valid, namespace resolves, and a tool responds.

## 5. Tools available (read-only, scoped)

`search`, `get_node`, `neighbors`, `at_time`, `history`, `changes`,
`list_namespaces`, `schema`. Every result is filtered to your namespace;
cross-namespace edges are hidden unless both endpoints are visible.
