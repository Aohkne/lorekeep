# Lorekeep — Temporal Knowledge Graph for AI Agents via MCP

- **Date:** 2026-06-14
- **Status:** Draft, pending implementation
- **Author:** design session (Claude Code brainstorming)
- **Project root:** `/home/manhpt1/Workspace/lorekeep` (greenfield)

## 1. Overview

Lorekeep compiles a team's raw documentation into a **temporal knowledge graph** and serves it, read-only, to AI agents over the **Model Context Protocol (MCP)**. It applies Andrej Karpathy's "LLM Wiki" idea — treat raw docs as source code and the compiled graph as the executable — and extends it with two additions the existing open-source landscape does not provide together:

1. **Strictly file-based storage** (`facts.jsonl`), for privacy and portability.
2. **Namespace-scoped permission**, for team-level and multi-team use rather than a single local user.

The system is **compile-only**: a curator (human + LLM) compiles raw docs into the graph as a build step. Agents only **read**. This removes the hardest problems of graph systems — concurrency, multi-writer transactions, conflict resolution — and is what makes a file-based, permissioned, temporal graph tractable.

### Goals

**North star:** Lorekeep exists to let an agent reason about a domain **systematically and with complete information** — not to maximize memory-recall benchmark scores. Memory benchmarks (LoCoMo, LongMemEval) are parity checks, not the objective. The real measures are completeness, coherence, temporal correctness, and reasoning support (see §16).

- Compile raw team docs (markdown first) into a temporal knowledge graph stored as portable `facts.jsonl`.
- Serve the graph read-only to coding agents (Claude Code, Cursor, Codex) over MCP, stdio-first.
- Enforce coarse per-namespace permission at the query layer; derive namespace from directory structure.
- Keep `facts.jsonl` as the single source of truth and the sync unit (git or S3), with no binary store committed.
- Support multi-device workflows: clone/pull, spawn local MCP server, done.

### Non-goals (v1)

- Live agent writes / memory-style mutation (no write path for agents).
- Human-readable wiki generation (`wiki.md` views derived from the graph) — phase 2.
- Embeddings / hybrid vector search — phase 2, only if scale demands.
- Ingest connectors for Confluence/PDF/URL — phase 2.
- OIDC / SSO auth — v1 uses local token→namespace mapping.
- Streamable-HTTP team server — phase 2 (stdio covers coding agents).

## 2. Background

### Karpathy "LLM Knowledge Bases"

Karpathy frames raw research/docs as **source code** and a compiled, structured, human-readable wiki as the **executable**. Knowledge is processed **once** at compile time, not re-processed per query, which lets mid-sized datasets skip the complexity of vector databases and RAG. Lorekeep adopts the compile step but emits a **temporal knowledge graph** rather than (or, in phase 2, in addition to) prose wiki pages.

### OSS gap

| Requirement | mcp-mind-palace | mcp-knowledge-graph | mem0 | cognee |
|---|---|---|---|---|
| Strictly file-based | yes | yes (JSON) | no (vector/Neo4j) | no (graph DB) |
| Temporal knowledge graph | no | no | partial | partial |
| Karpathy compile step | no | no | no | partial |
| Team-level permission | no (local) | no (local) | partial (DB) | partial (DB) |
| MCP | yes | yes | yes | yes |

No existing project covers *strictly-file + temporal graph + compile step + namespace permission + MCP*. Lorekeep fills that intersection.

## 3. Key decisions (locked)

| # | Decision | Rationale |
|---|---|---|
| D1 | Compile-only (Karpathy) | Single writer removes graph concurrency hell; simplest permission model (read-scoping). |
| D2 | Per-namespace coarse permission | Derives from directory tree; file-native; maps to filesystem/git; extendable to page overrides later. |
| D3 | Python + FastMCP | Richest LLM/markdown/MCP ecosystem; compile-heavy logic favors Python. |
| D4 | Mid-org target (≈5k facts, 5–15 teams) | Karpathy sweet-spot; FTS/grep sufficient, no embeddings needed yet. |
| D5 | Temporal knowledge graph | Facts carry `valid_from`/`valid_to`; supports "what was true at T", history, diffs. |
| D6 | `facts.jsonl` as store + sync unit | Plain text, line-based git diffs, S3-streamable; no binary store committed. |
| D7 | Query via networkx in-memory; optional local FTS cache | Store is the sync unit; no rebuild-on-sync; cache is local-only, derived. |
| D8 | stdio-first transport | Every coding agent spawns the local server reading the repo's `facts.jsonl`; zero servers, max privacy. |
| D9 | Coding-agent integration is v1 priority | `lorekeep mcp add` writes Claude Code / Cursor / Codex configs. |
| D10 | Extract LLM pluggable, default API, ollama option | Quality by default; data leaves only at compile time (curator-run); ollama for strict privacy. |
| D11 | Split into two implementation plans | (a) compile pipeline, (b) store + permission + MCP + integrations. |

## 4. Architecture

```
                COMPILE (offline, curator)                         SYNC (git / S3)
raw/teams/<ns>/*.md ──► ingest ──► extract(LLM) ──► resolve ──► writer ──► facts.jsonl + manifest + schema
                                                                                  │
                                                            ┌─────────────────────┴─────────────────────┐
                                                            ▼ (clone/pull)                              ▼
                                               every device / coding agent                         S3 object store

                SERVE + QUERY (runtime, per device)
facts.jsonl ──load──► store (networkx DiGraph, temporal) ──► perm guard (allowed_ns) ──► MCP tools ──► agent
                                  │
                                  └─ optional: .lorekeep/cache.sqlite (FTS5, local, .gitignored)
```

## 5. Repository layout

```
lorekeep/
├── raw/teams/<ns>/*.md        # source docs (committed) — Karpathy "source code"
├── graph/                     # compiled artifacts (committed) — "executable"
│   ├── facts.jsonl            # THE store: nodes + edges + temporal + ns, 1 fact/line
│   ├── manifest.json          # provenance: raw→fact map, chunk hashes, compile run, errors
│   └── schema.json            # node/edge type definitions
├── .lorekeep/                   # LOCAL only (.gitignored)
│   ├── cache.sqlite           # FTS5 cache, rebuilt per device
│   └── config.yaml            # namespace defs, token/git-user→ns map, LLM provider config
├── src/lorekeep/
│   ├── compile/{ingest,extract,resolve,writer}.py
│   ├── store/{graph,fts}.py
│   ├── perm/ns.py
│   ├── integrations/{claude_code,cursor,codex}.py
│   ├── mcp_server.py
│   └── cli.py
├── tests/
├── docs/{quickstart,integrations/{claude-code,cursor,codex}}.md
└── pyproject.toml
```

## 6. `facts.jsonl` format

One JSON object per line. Two kinds: `node` and `edge`. Deterministic key order (sorted) for stable diffs.

```jsonl
{"kind":"node","id":"svc:payments","ns":["teams/backend"],"props":{"lang":"go","name":"payments-api"},"src":["raw/teams/backend/payments.md:12"],"type":"service","valid_from":"2024-01-15","valid_to":null}
{"kind":"edge","id":"e_001","type":"depends_on","from":"svc:payments","to":"svc:auth","ns":["teams/backend","teams/platform"],"props":{},"src":["raw/teams/backend/payments.md:20"],"valid_from":"2024-01-15","valid_to":"2025-03-01"}
```

- `valid_to: null` ⇒ still current. History = multiple edges with the same `from`/`to`/`type` and different validity windows.
- `ns` is a **set**. `["public"]` ⇒ visible to all agents.
- `src` is provenance (path:line) for every fact ⇒ audit, trust, incremental re-compile, and agent citations.

### `schema.json`

Defines allowed node types and edge types with their property schemas. The extractor is constrained to this schema (structured output / JSON-schema response) so the graph is typed and predictable. Schema version is part of the chunk hash (changing the schema forces re-extraction).

## 7. Components

Each component has one responsibility, a clear input/output interface, and is testable in isolation.

| Component | Input | Output | Responsibility |
|---|---|---|---|
| `compile/ingest` | raw path | `[DocChunk]` | Parse markdown into chunks with source location (path:line). Stateless reader. |
| `compile/extract` | chunk + schema | candidate facts | **The Karpathy compiler.** LLM-driven, provider-pluggable (API default, ollama option). Constrained to `schema.json`. Idempotent per chunk via hash cache. |
| `compile/resolve` | candidate facts | clean facts | Entity dedup (alias → canonical id), validate edge endpoints exist, enforce ns-consistency, quarantine malformed facts. |
| `compile/writer` | clean facts | `facts.jsonl` + `manifest.json` | **Deterministic emit**: facts sorted by `(kind, type, id)`, sorted JSON keys, stable formatting ⇒ byte-identical output for unchanged input ⇒ clean git diffs. |
| `store/graph` | `facts.jsonl` | networkx `MultiDiGraph` (temporal) | Load + query API: `get_node`, `neighbors`, `traverse`, `temporal_slice(t)`, `history(id)`, `changes(t1,t2)`. Pure functions over the loaded graph; no I/O after load. |
| `perm/ns` | `allowed_ns` (set) | filter / guard | **Single permission chokepoint.** Every store query passes through here. |
| `store/fts` (optional) | `facts.jsonl` | `cache.sqlite` | FTS5 over node text/props for text search. Local, `.gitignored`, rebuilt from `facts.jsonl`. Falls back to in-memory scan if absent. |
| `integrations/*` | agent type, scope, ns | config file + memory snippet | Write Claude Code / Cursor / Codex MCP config; emit agent-memory text. |
| `mcp_server` | store + perm | MCP tool calls | FastMCP server, stdio (default) / streamable HTTP (opt-in). Loads store once; enforces permission per request. |
| `cli` | — | — | `compile`, `serve`, `query`, `check`, `mcp add`, `doctor`, `eval`. |
| `eval/{construction,retrieval,reasoning}` | corpus / graph / agent tasks | scores + snapshots | Three-tier evaluation (§16). `lorekeep eval {construction\|retrieval\|reasoning}`; results snapshot to `eval/results/`. |

### Dependency order

`ingest → extract → resolve → writer` (compile chain); `store → perm → mcp_server` (serve chain). Compile and serve share only `facts.jsonl` + `schema.json`. The two chains can be developed and tested independently — this is the seam for the two-plan split (§17).

## 8. Permission model

Namespace is derived from directory structure: every fact extracted from `raw/teams/<ns>/*` is tagged `ns: ["teams/<ns>"]` (or multiple if shared). `["public"]` is globally visible.

### Identity → namespace

- v1: `LOREKEEP_NS` env var, or `.lorekeep/config.yaml` mapping (`token` or `git_user` → `allowed_ns`).
- The permission engine only needs the `allowed_ns` set; the source of that set is pluggable for future OIDC/team-sync.

### Visibility rules (strict, deny-by-default)

Define the effective allowed set as `A' = A ∪ {"public"}` (every caller implicitly sees `public`).

- **Node** visible ⇔ `node.ns ∩ A' ≠ ∅`.
- **Edge** visible ⇔ *every* endpoint node is visible to `A'`, **and** `edge.ns ∩ A' ≠ ∅`.
- Empty/unknown `A` ⇒ `A' = {"public"}` ⇒ sees only `public` facts.

The guard injects these filters into every query at the MCP boundary. There is no raw bypass path. The strict endpoint rule prevents leakage of a cross-namespace neighbor's existence through an edge.

## 9. Temporal model

Every node and edge carries `valid_from` (ISO date) and `valid_to` (ISO date or `null`). Queries:

- `at_time(t)` — subgraph of facts where `valid_from ≤ t < valid_to` (treating `null` as +∞).
- `history(id)` — all versions of an entity and all edges touching it, ordered by `valid_from`.
- `changes(t1, t2)` — edges whose validity window began or ended within `[t1, t2)`.

Temporal filtering composes with permission filtering: a temporal query returns only facts the caller is allowed to see.

## 10. Compile pipeline

### Steps

1. `ingest` reads `raw/`, produces chunks with `path:line` source.
2. `extract` (LLM, schema-constrained) emits candidate node/edge facts with temporal + ns from each chunk.
3. `resolve` dedups entities (aliases → canonical id), validates graph integrity, quarantines bad facts.
4. `writer` emits deterministic `facts.jsonl` + `manifest.json`.

### Determinism

`writer` sorts facts by `(kind, type, id)`, serializes with sorted keys and fixed separators, one object per line terminated by `\n`. Re-compiling unchanged input yields byte-identical output. Changed input yields a minimal diff. This is essential for git-based sync and review.

### Incremental compile

`manifest.json` maps each `chunk_hash` (hash of normalized chunk text + schema version) to the fact ids it produced, plus cached extraction output (under `.lorekeep/`, local). Re-compile skips unchanged chunks unless `--force`. Unchanged chunks ⇒ identical contribution ⇒ stable diffs.

### Error handling (compile)

- LLM failure / unparseable chunk ⇒ log to `manifest.errors`, skip chunk, continue. Partial compile is valid; re-run fixes gaps.
- Malformed candidate fact ⇒ `resolve` quarantines to `manifest.quarantine`, drops from output.
- Edge with missing endpoint ⇒ dropped (or stubbed, configurable).
- Provider unavailable ⇒ compile aborts with clear message; already-written partial results are not merged unless `--allow-partial`.

## 11. MCP server

- **Transport:** stdio (default, for coding agents); streamable HTTP (opt-in, phase-2 team server).
- **Load:** `facts.jsonl` loaded once at startup into the in-memory store; optional FTS cache rebuilt lazily.
- **Auth → ns:** read `LOREKEEP_NS` / config at startup; every tool call is scoped through `perm`.

### Tools (read-only, v1)

| Tool | Purpose |
|---|---|
| `search(query, limit)` | Text search (FTS if cached, else scan) within ns scope. |
| `get_node(id)` | Node + props + provenance `src`. |
| `neighbors(id, edge_type?, depth?)` | Traverse, temporal-aware, ns-scoped. |
| `at_time(time)` | Snapshot of facts valid at `time`. |
| `history(id)` | Temporal versions of an entity. |
| `changes(from_t, to_t)` | Diff of facts between times. |
| `list_namespaces()` | Namespaces visible to this caller (debug). |
| `schema()` | Available node/edge types. |

Every tool is auto-scoped by `allowed_ns`. The agent surface is purely read-only, minimizing attack surface. Compile is via CLI, never via MCP.

### Output shape

Compact JSON per result: `id`, `type`, short summary, `props`, `src` provenance, and a `next` cursor for pagination. Large traversals paginate to control token cost for agents.

## 12. Coding-agent integration (v1 priority)

`lorekeep mcp add --agent {claude|cursor|codex} [--scope project|user] [--ns teams/<x>]` writes the correct config and prints an agent-memory snippet to paste into `CLAUDE.md` / `.cursorrules` / `AGENTS.md`.

> **Install source.** The snippets use `uvx lorekeep`, which assumes the package is published to PyPI. For a private/team repo, `mcp add` substitutes the install source — either `uvx --from git+https://<repo>/lorekeep.git lorekeep serve`, or a local install (`uv tool install .` then `lorekeep serve`) if the device has a checkout. The CLI detects this from `.lorekeep/config.yaml` (`install_source`) so the emitted config is correct for each deployment.

### Claude Code — `.mcp.json` (project scope)

```json
{
  "mcpServers": {
    "lorekeep": {
      "command": "uvx",
      "args": ["lorekeep", "serve", "--transport", "stdio"],
      "env": { "LOREKEEP_NS": "teams/backend" }
    }
  }
}
```

### Cursor — `.cursor/mcp.json`

```json
{
  "mcpServers": {
    "lorekeep": {
      "command": "uvx",
      "args": ["lorekeep", "serve", "--transport", "stdio"]
    }
  }
}
```

### Codex — `~/.codex/config.toml`

```toml
[mcp_servers.lorekeep]
command = "uvx"
args = ["lorekeep", "serve", "--transport", "stdio"]
env = { LOREKEEP_NS = "teams/backend" }
```

### Agent-memory snippet (printed by `mcp add`)

```markdown
## Lorekeep knowledge base (MCP)
Before answering architecture/code/domain questions, query Lorekeep:
search(q) → get_node(id) → neighbors / at_time / history as needed.
Always cite `src` provenance. Knowledge is namespace-scoped — if a fact is
missing, it may be outside your scope, not nonexistent.
```

### `lorekeep doctor`

Verifies: install, config file present/valid, `facts.jsonl` loads, ns mapping resolves, and the MCP tools respond. Fast feedback for onboarding.

## 13. Sync and multi-device

- **git (primary):** commit `raw/` + `graph/` (facts.jsonl, manifest, schema). Each device clones/pulls and spawns its local MCP server. No binary store is committed; the FTS cache is `.gitignored` and rebuilt locally.
- **S3 (alternative):** `aws s3 sync` the same paths to an object store; devices sync down.
- **Write conflicts:** compile is an explicit, curator-run CLI. v1 assumes a single compile host per period, or git-PR-based compile (line-based JSONL merges cleanly). Concurrent compiles from two devices are out of scope for v1.
- **Future scale (>50k facts):** partition `facts.jsonl` to Parquet on S3; query via DuckDB/Polars directly on objects.

## 14. Error handling (consolidated)

- **Compile:** skip-and-log on LLM/parse failure; quarantine malformed facts; drop/stub dangling edges; abort on provider outage unless `--allow-partial`.
- **Serve/load:** skip corrupt lines with a warning; do not crash the server.
- **Permission:** deny-by-default; unknown ns ⇒ see only `public`; never leak cross-namespace existence.
- **Cache:** missing FTS cache ⇒ fall back to scan, rebuild lazily.
- **Integration:** `doctor` surfaces config/load/ns/tool failures before the agent hits them.

## 15. Testing strategy

- **Unit per component:** ingest parsing, extract with a mock LLM (fixture outputs), resolve dedup logic, store temporal/traversal queries, permission filtering.
- **Property tests:**
  - *Determinism* — same input ⇒ byte-identical `facts.jsonl`.
  - *ns-closure* — no query ever returns a fact outside the caller's `allowed_ns` (incl. edge-endpoint leakage).
  - *temporal-validity* — `at_time` only returns facts whose window contains `t`.
- **Integration:** small raw fixture → compile → serve → MCP tool calls assert filtered results and provenance.
- **Golden tests:** snapshot `facts.jsonl` for a fixture corpus; diff catches regressions.
- Compile-only ⇒ no concurrency to test.

## 16. Evaluation

**North star = systematic thinking with complete information.** Memory-recall benchmarks are parity checks, not the objective. Evaluation measures five properties, with *reasoning support* as the headline metric:

| Metric family | What it measures | Tier |
|---|---|---|
| **Completeness** | Salient facts captured from raw (coverage); nothing missing in scope | 1 |
| **Coherence** | No contradictions, no duplicate-entity leaks, consistent graph | 1 |
| **Temporal correctness** | `at_time` / `history` / `changes` return correct facts | 1+2 |
| **Retrievability** | Agent finds the facts a question needs (multi-hop, temporal QA) | 2 |
| **Reasoning support** ⭐ | Systematic-reasoning answer quality vs baseline RAG / no-KB | 3 |

### Tier 1 — Construction quality (CI, every commit)

Evaluates the **compiler**, not the agent.

- Extraction P/R/F1 vs a **gold-annotated corpus** (human-authored `facts.jsonl` reference), per node/edge type.
- Entity-resolution F1 + false-merge rate.
- Graph structure: coverage of salient facts, average degree, dangling-edge rate, contradiction rate.
- Determinism: byte-identical re-compile of unchanged input (property test, §15).
- Datasets: a small in-repo **gold corpus** of team-doc-style fixtures (primary); optionally validate extractor generalization on document-level relation-extraction benchmarks (DocRED-family) — not required for v1.
- Owner: **Plan A**.

### Tier 2 — Retrieval + temporal QA (CI / per release)

Evaluates whether the **query path** returns correct facts.

- Multi-hop QA: HotpotQA / 2WikiMultihopQA / MuSiQue — agent answers using Lorekeep tools; EM/F1.
- Temporal QA: CronQuestions / TimeQuestions + the temporal subset of Atlas — measures `at_time` / `history` / `changes`. This is the industry weak spot (specialized memory systems drop to ~20% on temporal reasoning per Atlas) and Lorekeep's core bet.
- Memory parity: LongMemEval / LoCoMo — sanity that graph retrieval is no worse than vector memory on long-horizon recall. **Not optimized as a target.**
- Owner: **Plan B**.

### Tier 3 — Systematic-thinking reasoning eval (north star, per release / manual)

The actual goal. No off-the-shelf benchmark fits team-doc systematic reasoning, so we build a small bespoke one.

- **Lorekeep-Reason**: curated team-doc corpora + multi-step reasoning tasks (e.g. "trace the blast radius of deprecating service X across teams and time", "reconstruct the decision history and current state of ADR-Y") + reference answers + rubric.
- Method: a coding agent (Claude Code / Cursor / Codex) solves each task under three conditions — (a) with Lorekeep, (b) with raw-doc RAG baseline, (c) with no knowledge base.
- Metrics: **LLM-judge rubric** (completeness, correctness, temporal accuracy, provenance use, reasoning coherence) + objective sub-questions; multiple judges to control variance.
- v1 ships a minimal harness + 5–10 seed tasks; the full dataset grows incrementally.

### Component & CLI

`eval/{construction,retrieval,reasoning}.py`; `lorekeep eval {construction|retrieval|reasoning}`. Results snapshot to `eval/results/<date>-<commit>.json` (committed, like golden tests) so regressions surface in diffs.

### Notes

- Memory benchmarks are recall-oriented; chasing their scores mis-optimizes away from the north star. They stay parity checks.
- Bespoke gold corpus + Lorekeep-Reason cost real annotation effort — start small, grow incrementally.
- LLM-judge variance → rubric + multi-judge + calibration against human labels on a subset.
- Temporal QA being hard (Atlas ~20%) is exactly why a structured temporal graph is the bet; Tier-2 temporal eval must stress this heavily.

## 17. Scope and decomposition

### v1 IN

ingest (markdown) + extract + resolve + writer + `facts.jsonl`/manifest/schema + store (networkx, temporal) + permission + optional FTS cache + MCP (read + temporal, stdio) + `mcp add` for Claude Code/Cursor/Codex + `doctor` + docs + tests + **Tier-1 construction eval (CI)** + **Tier-2 retrieval/temporal-QA smoke**.

### v1 OUT (phase 2+)

`wiki.md` views, Parquet/DuckDB at scale, streamable-HTTP team server, OIDC/SSO, ingest connectors (Confluence/PDF/URL), embeddings/hybrid search, agent write tools, **full Lorekeep-Reason dataset (Tier-3 scaling)**.

### Two-plan split

- **Plan A — Compile pipeline + construction eval:** `ingest → extract → resolve → writer`, schema, determinism, incremental cache, error handling, tests, **Tier-1 construction eval (extraction P/R/F1 vs gold)**. Deliverable: `facts.jsonl` produced deterministically from `raw/`, with measured extraction quality.
- **Plan B — Store + permission + MCP + integrations + retrieval eval:** `store` (load + temporal queries), `perm`, optional FTS cache, `mcp_server` (stdio tools), `mcp add` for the three agents, `doctor`, docs, tests, **Tier-2 retrieval/temporal-QA eval**. Deliverable: a coding agent reads the scoped temporal graph, with measured retrieval/temporal accuracy.

Plan A and Plan B share only `facts.jsonl` + `schema.json`; they can be developed in parallel against that contract, but Plan B is easiest to validate once Plan A produces a real `facts.jsonl`.

## 18. Tech stack

Python 3.11+ · FastMCP · networkx · pydantic (fact/schema models) · pyyaml (config) · mistune or markdown-it-py (markdown) · sqlite3 FTS5 (stdlib) · litellm (provider abstraction: OpenAI/Anthropic/ollama) · uv for packaging/publish (`uvx lorekeep`).

## 19. Open questions / risks

- **Extraction quality vs privacy tension (D10):** default API sends raw docs to a provider at compile time. Acceptable because compile is curator-run and the stored artifact is pure-file, but strict-privacy deployments must switch to ollama (lower quality, GPU cost). Documented; not a blocker.
- **Entity resolution correctness:** alias → canonical id merging is the riskiest compile logic; needs strong fixtures and a quarantine/review path. Flagged for Plan A.
- **Cross-namespace edge UX:** the strict endpoint rule hides cross-team edges unless both sides are allowed. A `public` projection mechanism mitigates; verify it covers real team workflows during Plan B.
- **Determinism vs LLM non-determinism:** LLM extraction is inherently variable; determinism is achieved via per-chunk hash caching of extraction output, not by re-running the LLM. Unchanged input ⇒ cached ⇒ identical. Documented in Plan A.
- **Benchmark fit (eval, §16):** no off-the-shelf benchmark matches team-doc systematic reasoning. Memory benchmarks (LoCoMo, LongMemEval) are recall-oriented and will be treated as parity checks only; optimizing them would pull the design away from the north star. Tier-3 (Lorekeep-Reason) must be built bespoke — real annotation cost, mitigated by starting with a small seed set. Temporal-QA benchmarks are the best external fit and the hardest (Atlas ~20%); they validate Lorekeep's core temporal bet.

## 20. References

- Karpathy "LLM Knowledge Bases" — compiler analogy (source code vs executable).
- `mcp-mind-palace`, `mcp-knowledge-graph` (Anthropic reference), `mem0`, `cognee`, **Zep (temporal KG)** — landscape comparison (§2).
- MCP specification — tool/resource model, stdio and streamable-HTTP transports.
- **Evaluation benchmarks:** LoCoMo, LongMemEval, BEAM, Atlas (memory / beyond-retrieval); HotpotQA, 2WikiMultihopQA, MuSiQue (multi-hop QA); CronQuestions, TimeQuestions (temporal KG QA); DocRED-family (document-level relation extraction).

- Karpathy "LLM Knowledge Bases" — compiler analogy (source code vs executable).
- `mcp-mind-palace`, `mcp-knowledge-graph` (Anthropic reference), `mem0`, `cognee` — landscape comparison (§2).
- MCP specification — tool/resource model, stdio and streamable-HTTP transports.
