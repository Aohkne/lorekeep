# Serving the knowledge graph to coding agents

Laputa resolves its data home with this precedence: explicit `LAPUTA_*` env >
`LAPUTA_HOME` > dev mode (`.laputa/` or `raw/` in CWD) > XDG default
(`~/.config/laputa` + `~/.local/share/laputa`).

## Installed use (recommended)

```bash
uvx laputa init                       # bootstrap ~/.config/laputa + ~/.local/share/laputa
# add your docs under ~/.local/share/laputa/raw/<ns>/
LAPUTA_PROVIDER=fake uvx laputa compile    # (or set a real provider in config)
uvx laputa mcp add --agent claude --ns <ns>
uvx laputa doctor
```

`mcp add` writes a **portable** `.mcp.json` (no machine path) when
`install_source` is `pypi` (the default from `init`):

```json
{"mcpServers": {"laputa": {"command": "uvx",
  "args": ["laputa", "serve", "--transport", "stdio"],
  "env": {"LAPUTA_NS": "<ns>"}}}}
```

## Local dev (repo co-located data)

From the Laputa source checkout (has `.laputa/` → auto dev mode):

```bash
uv run laputa compile      # reads repo raw/, writes repo graph/
uv run laputa serve
```

Force dev mode anywhere: `LAPUTA_DEV=1 laputa ...`.

## Custom knowledge base

```bash
LAPUTA_HOME=~/kb-work uvx laputa init
LAPUTA_HOME=~/kb-work uvx laputa compile
```

## Tools (read-only, scoped)

`search`, `get_node`, `neighbors`, `at_time`, `history`, `changes`,
`list_namespaces`, `schema`. Results are filtered to `LAPUTA_NS`; cross-namespace
edges are hidden unless both endpoints are visible.

## Connect once (lazy-reload)

The server loads `facts.jsonl` into memory and **lazy-reloads** it: every query
stats the file's mtime, and if it changed (after `laputa compile`) the graph is
rebuilt automatically. So the workflow is:

```bash
<edit raw/.../*.md>
uvx laputa compile          # rebuilds facts.jsonl
# next query from the agent sees the new graph — NO reconnect needed
```

Connect the MCP server **once**; memory updates via `compile` are visible
immediately. Reconnect is only needed for **code** changes (rare; the serve path
is stable) or **scope** changes (`.mcp.json` `LAPUTA_NS`).

