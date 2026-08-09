# Getting started

This walkthrough takes a new installation from an empty data home to a compiled
graph that a coding agent can query. It also makes clear which steps call an LLM
and which are deterministic local operations.

## 1. Install

Lorekeep requires Python 3.11+ and uv. Use `uvx` for an occasional command, or
install the tool when you want a long-running watcher/service:

```bash
uvx lorekeep version

uv tool install lorekeep
lorekeep version
```

From a source checkout:

```bash
git clone https://github.com/manhhailua/lorekeep.git
cd lorekeep
uv sync
uv run lorekeep version
```

## 2. Initialize the data home

```bash
lorekeep init
```

On the first interactive run, `init` asks for:

1. an extraction provider/model and either an inline local key or the name of an
   environment variable;
2. the personal/default namespace, initially `me`; and
3. your name and one-line bio.

It then performs an idempotent setup chain:

- writes `config.yaml` and stock schema v4;
- creates `raw/`, `graph/`, and `pending/`;
- writes `raw/<personal-ns>/about.md` and `profile.md`;
- detects installed Claude Code, Cursor, Codex, and opencode clients;
- writes their MCP configuration and supported session-end hooks;
- quick-imports available agent memory files without an LLM;
- runs the initial compile when a usable provider key exists; and
- starts `agent watch` in the background when the command is interactive and
  `--no-watch` was not passed.

Re-running `init` preserves existing config/schema and re-runs safe agent
detection/wiring. A non-interactive invocation does not start a background
watcher:

```bash
lorekeep init --yes --no-watch
```

If no provider key is available, initialization still creates the sources and
wires agents. Add a key before the first compile.

## 3. Configure extraction

All model values use LiteLLM's `{provider}/{model}` form:

```bash
lorekeep config set provider.model deepseek/deepseek-chat
lorekeep config set provider.api_key_env DEEPSEEK_API_KEY
export DEEPSEEK_API_KEY=...
```

An OpenRouter example:

```bash
lorekeep config set provider.model openrouter/deepseek/deepseek-chat
lorekeep config set provider.api_key_env OPENROUTER_API_KEY
export OPENROUTER_API_KEY=...
```

Native cloud providers normally need no `api_base`. Set `api_base` for a custom
OpenAI-compatible endpoint, or for Ollama when it is not at its normal local
address. The full validated example is
[`.lorekeep/config.yaml.example`](../../.lorekeep/config.yaml.example).

The provider is used during `compile`, `agent ingest`, and manual deep import.
MCP queries, quick capture, resolve, wiki generation, lint, status, and backup do
not call it.

## 4. Add source Markdown

Put documents under `<data-home>/raw/<namespace>/`. The first directory is the
fact namespace and therefore a permission boundary:

```bash
mkdir -p ~/.local/share/lorekeep/raw/backend
cp payments.md ~/.local/share/lorekeep/raw/backend/
```

From a source checkout, use `.lorekeep/raw/backend/`. To find/open your personal
profile source:

```bash
lorekeep agent profile
lorekeep agent profile --open
```

Edit raw files, not generated wiki pages. Raw Markdown plus schema and journals
are durable inputs; `facts.jsonl` and the wiki are reproducible outputs.

## 5. Compile and validate

```bash
lorekeep compile
lorekeep doctor
```

`compile` performs the complete normal pipeline: cached extraction from `raw/`,
candidate resolution, atomic graph/manifest write, journal replay/merge, and one
wiki generation from the final facts. Unchanged chunks reuse cached extraction
output, preserving byte-stable recompiles.

`doctor` requires a graph. It checks graph/schema loading, scoped MCP response,
and provider connectivity when a key is available. If setup intentionally has no
provider/graph yet, add source + provider and compile before running it.

## 6. Verify and wire coding agents

Inspect what Lorekeep sees:

```bash
lorekeep agent detect
lorekeep agent detect --json
```

`init` normally wires detected agents. Wire one client explicitly when needed:

```bash
lorekeep mcp add --agent claude --scope project --ns backend
lorekeep agent wire --agent codex --scope user --ns backend
```

`mcp add` is the focused one-client command and prints the recommended agent
instruction snippet. `agent wire` uses the shared agent registry and can wire all
detected clients; `--force` includes undetected clients. Both operations are
idempotent.

Restart the client after configuration/scope changes. The available MCP tools
are:

```text
search, get_node, neighbors, temporal_query, context,
propose_change, review_note
```

The client receives `backend` plus `public` in this example. A hidden namespace
is indistinguishable from a missing fact.

## 7. Teach the agent when to query

MCP wiring makes tools available; it does not force every model/client to call
them. Preserve the snippet printed by `init`/`mcp add` in the client's user or
project instructions. The recommended lookup flow is:

```text
context(section="status")
  → search(query)
  → get_node(id)
  → neighbors(id) and/or temporal_query(mode, params)
```

Ask questions using the domain terms or likely entity names already present in
the graph, and mention time or namespace when relevant. Examples:

- “Query Lorekeep for the current dependencies of `payments-api`; cite `src`. ”
- “What did the checkout architecture look like on 2025-03-01?”
- “Search Lorekeep before answering; if coverage is weak, say so.”

Agents can propose newly verified facts with `propose_change`, but those facts
remain pending until `resolve` or the watcher merges them.

## 8. Keep the graph current

Foreground watcher:

```bash
lorekeep agent watch
```

Persistent login/restart service:

```bash
lorekeep agent service install
lorekeep agent service status
```

The watcher reacts to raw/schema changes, pending journals, supported memory and
transcript sources, newly detected agents, package upgrades, and configured Git
backup. It does not schedule nightly lint or weekly suggestions; run those
commands directly when wanted.

Without a watcher, use the explicit operations:

```bash
lorekeep compile    # raw changed; uses the extraction provider
lorekeep resolve    # only journals changed; zero LLM calls
lorekeep wiki       # only re-project existing facts; zero LLM calls
```

The MCP server reloads `facts.jsonl` on the next query after its mtime changes,
so graph refreshes do not require reconnecting. Scope or MCP config changes do.

## 9. Back up for another device

Use a dedicated private remote:

```bash
lorekeep backup --init https://github.com/<you>/lorekeep-data.git
lorekeep backup
```

Raw docs, schema, and `pending/` journals are durable and synced. Config/secrets
and derived graph/wiki/cache files are ignored. Git sync is currently sequential;
simultaneous edits to the same tracked file may need an ordinary manual rebase
conflict resolution.

See [Backing up and syncing](backup.md) before restoring on a second device.

## Troubleshooting

- `serve` appears to hang: stdio transport waits for an MCP client. Use
  `timeout 3 lorekeep serve --transport stdio </dev/null` only as a boot smoke.
- Compile reports zero nodes: run `doctor`, then check the prefixed model, key,
  endpoint, and per-chunk errors in `manifest.json`/runtime logs.
- An agent cannot see a fact: inspect `context(section="namespaces")` and
  `context(section="status", topic="...")`; absence can mean out of scope.
- An agent never queries: confirm wiring with `agent detect`, restart it, and add
  the Lorekeep lookup snippet to its instructions.
- A daemon fails silently: run `agent service status`, then `lorekeep support`.

## Next

- [Compile and resolve](compile.md)
- [Import agent sessions](import.md)
- [Serve over MCP](serve.md)
- [Browse the wiki](wiki.md)
- [Data home and paths](data-home.md)
