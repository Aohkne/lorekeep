# Serving the knowledge graph to coding agents

Path resolution (env > `LOREKEEP_HOME` > dev mode > XDG) is covered in [data-home.md](data-home.md).

## Installed use (recommended)

```bash
uvx lorekeep init                     # bootstrap ~/.config/lorekeep + ~/.local/share/lorekeep
# add your docs under ~/.local/share/lorekeep/raw/<ns>/
LOREKEEP_PROVIDER=fake uvx lorekeep compile  # (or set a real provider in config)
uvx lorekeep mcp add --agent claude --ns <ns>
uvx lorekeep doctor

# Start the daemon to keep the graph up-to-date:
uvx lorekeep agent watch &
```

`mcp add` writes a **portable** `.mcp.json` (no machine path) when
`install_source` is `pypi` (the default from `init`):

```json
{"mcpServers": {"lorekeep": {"command": "uvx",
  "args": ["lorekeep", "serve", "--transport", "stdio"],
  "env": {"LOREKEEP_NS": "<ns>"}}}}
```

## Local dev (repo co-located data)

From the Lorekeep source checkout (has `.lorekeep/` → auto dev mode):

```bash
uv run lorekeep compile      # reads repo raw/, writes repo graph/
uv run lorekeep serve
```

Force dev mode anywhere: `LOREKEEP_DEV=1 lorekeep ...`.

## Custom knowledge base

```bash
LOREKEEP_HOME=~/kb-work uvx lorekeep init
LOREKEEP_HOME=~/kb-work uvx lorekeep compile
```

## Read tools (8 tools, scoped)

`search`, `get_node`, `neighbors`, `at_time`, `history`, `changes`,
`list_namespaces`, `schema`. Results are filtered to `LOREKEEP_NS`; cross-namespace
edges are hidden unless both endpoints are visible.

## Write tools (5 tools, journal-based)

Agents contribute knowledge during conversation at **zero LLM cost**. Facts
are appended to `pending/` journals and merged into the graph on the next
resolve pass.

| Tool | Purpose | Confidence |
|---|---|---|
| `propose_fact(fact, confidence, ns)` | Propose a new node or edge | Agent-estimated (0-1) |
| `link_facts(from_id, to_id, type, confidence, ns)` | Create an edge | Typically ≥ 0.8 |
| `flag_contradiction(a, b, description, ns)` | Report conflicting facts | N/A |
| `update_fact(id, props, confidence, ns)` | Update existing fact props | 0.5-0.8 |
| `suggest_improvement(description, ns)` | Suggest gap or improvement | N/A |

**Confidence guidance for agents:**
- ≥ 0.8: explicit claim with source citation. "The codebase shows service X uses database Y."
- 0.5-0.8: implied without explicit source. "Based on the architecture, X likely depends on Y."
- < 0.5: speculation — these are quarantined, not merged.

Facts become visible after the next resolve pass (every 5 min or 50 pending
entries when daemon is running; or run `lorekeep resolve` manually).

## Keeping the graph current

Three approaches, from fully automatic to manual:

```bash
# 1. Daemon (recommended) — fully autonomous
uvx lorekeep agent watch
# Watches raw/ → auto-compile on change
# Watches pending/ → auto-resolve every 5 min / 50 writes
# Nightly lint + weekly suggestions

# 2. Manual with cron — scheduled
# */5 * * * * cd /path/to/lorekeep && uvx lorekeep resolve
# 0 3 * * *   cd /path/to/lorekeep && uvx lorekeep agent lint

# 3. Manual — curator-triggered
uvx lorekeep compile          # rebuild from raw/
uvx lorekeep resolve          # merge pending journals
uvx lorekeep agent lint       # health check
```

## Connect once (lazy-reload)

The server loads `facts.jsonl` into memory and **lazy-reloads** it: every query
stats the file's mtime, and if it changed (after compile or resolve) the graph is
rebuilt automatically. So the workflow is:

```bash
<edit raw/.../*.md>
uvx lorekeep compile          # rebuilds facts.jsonl
# OR: agent proposes facts during conversation
#      (daemon auto-resolves)
# next query from the agent sees the new graph — NO reconnect needed
```

Connect the MCP server **once**; graph updates via `compile` or `resolve` are
visible immediately. Reconnect is only needed for **code** changes (rare; the
serve path is stable) or **scope** changes (`.mcp.json` `LOREKEEP_NS`).
