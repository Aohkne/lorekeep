# Laputa

**A temporal knowledge graph for AI agents, served read-only over MCP.**

Laputa compiles a team's raw documentation into a versioned, time-aware
knowledge graph (`facts.jsonl`) and exposes it to coding agents (Claude Code,
Cursor, Codex) through the Model Context Protocol — with per-namespace
permission and zero servers to run.

It applies Andrej Karpathy's "LLM Knowledge Base" idea: raw docs are the
**source code**, the compiled graph is the **executable**. Knowledge is
processed once at compile time, not re-RAG'd on every query.

---

## Why

Existing tools each miss part of what a team needs:

| | file-based | temporal KG | compile step | team permission | MCP |
|---|---|---|---|---|---|
| Obsidian + MCP | ✅ | ❌ | ❌ | ❌ | ✅ |
| mcp-knowledge-graph | ✅ | ❌ | ❌ | ❌ (local) | ✅ |
| mem0 / cognee | ❌ (DB) | partial | ❌ | partial (DB) | ✅ |

Laputa targets the gap: **strictly file-based + temporal graph + compile-once +
namespace-scoped permission + MCP** — for team-level (not just single-user)
knowledge.

## Features

- **Compile-only** — a curator (human + LLM) builds the graph; agents only read.
  No write path, no concurrency hell, deterministic output.
- **File-sovereign** — `facts.jsonl` (one fact per line, sorted) is the single
  source of truth and the sync unit (git or S3). No binary store committed.
- **Temporal** — every fact carries `valid_from`/`valid_to` (half-open
  `[from, to)`); query "what was true at *T*", history, diffs.
- **Namespace permission** — facts are tagged `ns` from the directory tree
  (`raw/teams/<ns>/`); agents scoped to namespaces; cross-namespace edges
  hidden unless both endpoints are visible. Deny-by-default.
- **MCP, stdio-first** — `laputa serve` exposes 8 read-only tools; `laputa mcp add`
  wires Claude Code / Cursor / Codex. No server process to babysit.
- **Lazy-reload** — `laputa compile` updates the graph; the MCP server
  auto-refreshes on the next query. Connect once, use forever.
- **Provider-pluggable extraction** — litellm (OpenAI / Anthropic /
  DashScope/Qwen / Ollama). Strict-privacy → Ollama, fully local.
- **Tier-1 eval** — extraction P/R/F1 vs a gold corpus, entity-resolution F1,
  graph-structure metrics, determinism property tests.

## Install

```bash
# from a clone (PyPI publish pending):
git clone <your-laputa-repo> laputa && cd laputa
uv tool install .                 # installs the `laputa` command
```

Once published to PyPI it becomes simply `uvx laputa …`.

## Quickstart

```bash
# 1. bootstrap a data home (~/.config/laputa + ~/.local/share/laputa)
uvx laputa init

# 2. add docs under the data home's raw/teams/<namespace>/
mkdir -p ~/.local/share/laputa/raw/teams/backend
cp your-docs.md ~/.local/share/laputa/raw/teams/backend/

# 3. set a provider (edit ~/.config/laputa/config.yaml), then compile
uvx laputa compile                # raw/*.md -> graph/facts.jsonl

# 4. wire a coding agent (writes a portable .mcp.json)
uvx laputa mcp add --agent claude --ns teams/backend

# 5. verify
uvx laputa doctor
```

Restart Claude Code → the 8 Laputa tools are available, scoped to your namespace.

## How it works

```
                       COMPILE (offline, curator)                     SYNC
raw/teams/<ns>/*.md ──► ingest ──► extract(LLM) ──► resolve ──► writer ──► facts.jsonl
                                                                            │
                                          ┌─────────────────────────────────┘
                                          ▼  (git pull / aws s3 sync)
                    SERVE + QUERY (runtime, per device)
facts.jsonl ──load──► GraphStore (networkx, temporal) ──► ScopedGraph (ns) ──► MCP ──► agent
                              ▲                                  │
                              └── lazy-reload on mtime change ◄──┘
```

**Pipeline** (`ingest → extract → resolve → writer`): markdown is chunked with
provenance; an LLM extracts schema-constrained nodes/edges with temporal +
namespace tags; aliases collapse to canonical entities; a deterministic writer
emits sorted, byte-stable `facts.jsonl` + a `manifest.json` (provenance +
errors + quarantine). Re-compiling unchanged input is byte-identical (per-chunk
hash cache), so git diffs stay clean.

**Serve**: `GraphStore` loads `facts.jsonl` into a networkx graph with temporal
queries. `ScopedGraph` is the single permission chokepoint — every query is
filtered through strict visibility rules. The FastMCP server is a thin layer of
read-only tools over `ScopedGraph`. It lazy-reloads when `facts.jsonl` changes,
so `compile` is instantly visible without reconnecting.

## Concepts

**fact** — one line of `facts.jsonl`, a `node` or `edge`:
```jsonl
{"kind":"node","id":"svc:payments","type":"service","ns":["teams/backend"],"valid_from":"2024-01-15","valid_to":null,"props":{"lang":"go"},"src":["raw/teams/backend/payments.md:12"]}
{"kind":"edge","id":"e_depends_on_0001","type":"depends_on","from":"svc:payments","to":"svc:auth","ns":["teams/backend"],"valid_from":"2024-01-15","valid_to":"2025-03-01","props":{},"src":["...:20"]}
```
- `ns` — namespace set; `["public"]` is globally visible.
- `valid_to: null` ⇒ current. History = multiple edges, same endpoints, different windows.
- `src` — provenance to raw doc line (audit, incremental re-compile, agent citations).

**Permission** — effective_ns = allowed ∪ {public}. Node visible iff
`ns ∩ effective_ns ≠ ∅`. Edge visible iff **both** endpoints visible **and**
`edge.ns ∩ effective_ns ≠ ∅`. Deny-by-default; an edge never reveals a
neighbor the caller can't see.

**Temporal queries** — `at_time(T)` (snapshot of facts valid at T, half-open
`[from,to)`), `history(id)` (versions of an entity), `changes(t1,t2)` (edges
that began/ended in the window).

## MCP tools (read-only, scoped)

`search` · `get_node` · `neighbors` · `at_time` · `history` · `changes` ·
`list_namespaces` · `schema`. Every result is filtered to the caller's
namespace.

## Configuration

`config.yaml` (resolved by precedence: explicit `LAPUTA_*` env > `LAPUTA_HOME` >
dev marker > XDG):
```yaml
provider:
  model: openai/qwen-plus                              # litellm model string
  api_base: https://dashscope-intl.aliyuncs.com/compatible-mode/v1
  api_key_env: DASHSCOPE_API_KEY                       # env var name (preferred)
  api_key: null                                        # or inline (gitignored config only)
ns:
  default: [public]
install_source: pypi                                   # pypi = portable .mcp.json
```
API keys never live in committed files — use `api_key_env` (env) or inline
`api_key` in the gitignored config only. Examples (DashScope / OpenAI / Ollama)
in [`.laputa/config.yaml.example`](.laputa/config.yaml.example).

## Data home & dev mode

Path resolution (high → low): explicit `LAPUTA_*` env → `LAPUTA_HOME` →
**dev mode** (`.laputa/` or `raw/` in CWD; auto-detected in a source checkout)
→ XDG (`~/.config/laputa`, `~/.local/share/laputa`). So:

- **Installed**: `uvx laputa init` bootstraps the XDG home.
- **Local dev**: from the repo, `uv run laputa compile` uses the repo's
  `raw/` + `graph/` (zero migration).
- **Custom KB**: `LAPUTA_HOME=~/kb-work uvx laputa …`.

See [`docs/compile.md`](docs/compile.md) and [`docs/serve.md`](docs/serve.md).

## Evaluation

Tier-1 (CI): extraction P/R/F1 vs a gold corpus, entity-resolution pairwise F1,
graph-structure metrics, determinism. Run: `uvx laputa eval`. The north star is
*systematic thinking with complete information* — memory-recall benchmarks
(LoCoMo, LongMemEval) are parity checks, not the optimization target. See the
[design spec](docs/superpowers/specs/2026-06-14-laputa-temporal-kg-mcp-design.md) §16.

## Project layout

```
src/laputa/
  models.py            shared contract (Node/Edge/Schema/Manifest)
  facts_io.py          facts.jsonl loader (store + eval)
  paths.py             4-tier path resolution (env/home/dev/XDG)
  defaults.py          default schema + config (for `init`)
  config.py, schema_io.py
  compile/{ingest,extract,resolve,writer}.py    the compile pipeline
  compile/providers.py                          LLMProvider (Fake/LiteLLM)
  store/{graph,fts}.py                          GraphStore + optional FTS cache
  perm/ns.py                                    ScopedGraph permission chokepoint
  mcp_server.py                                 FastMCP + 8 read tools (lazy-reload)
  integrations/{claude_code,cursor,codex,common}.py
  pipeline.py, cli.py
  eval/{gold,construction,retrieval}.py
tests/                 ~106 tests
docs/                  compile.md, serve.md, specs/, plans/
```

## Status

**v1** — compile pipeline + serve (store/permission/MCP/integrations) + data-home
+ dev mode + lazy-reload, all merged to `main`, ~106 tests green.

Roadmap (phase 2+): PyPI publish, streamable-HTTP team server, OIDC/SSO,
embeddings/hybrid search, `wiki.md` views, full Tier-2 benchmark datasets
(HotpotQA/CronQuestions) and the bespoke Tier-3 Laputa-Reason eval.

## Documentation

- [Compile quickstart](docs/compile.md)
- [Serve to coding agents](docs/serve.md)
- [Design spec (architecture, permission, temporal, eval)](docs/superpowers/specs/2026-06-14-laputa-temporal-kg-mcp-design.md)
- Implementation plans: [A compile](docs/superpowers/plans/2026-06-14-laputa-plan-a-compile-pipeline.md),
  [B serve](docs/superpowers/plans/2026-06-14-laputa-plan-b-serve-mcp.md),
  [C data-home](docs/superpowers/plans/2026-06-15-laputa-plan-c-data-home-dev-mode.md)

## License

TBD.
