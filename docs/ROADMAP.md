# Roadmap

> Direction, not commitments. No dates. “Current” below means implemented in
> this repository; everything else is explicitly planned or under evaluation.

## North star

Lorekeep aims to be a file-sovereign second brain shared by a person, their
coding agents, devices, and—eventually—their team. The useful outcome is not
merely remembering text: agents should retrieve complete, coherent,
temporally-correct, source-backed knowledge without reprocessing the corpus on
every question.

## Current foundation

- Schema v4 / ontology v2.1 for people, software/work entities, typed
  relationships, summaries, descriptions, aliases, provenance, and validity
  windows.
- Compile-time extraction with chunk cache, deterministic resolve/publication,
  manifest diagnostics, and human-readable Obsidian/Tolaria wiki projection.
- Exactly eight MCP tools plus three passive resources, with deny-by-default
  namespace filtering at `ScopedGraph`.
- Append-only, process-locked agent journals; confidence-gated resolve; atomic
  graph publication; lazy server reload.
- Integration registry for Claude Code, Cursor, Codex, opencode, and Grok Build,
  including config wiring, supported hooks, memory quick import, and bounded
  transcript capture.
- Event-driven watcher for raw/schema/journal/session changes, idempotent agent
  wiring, self-heal, backup sync, and installed-version restart.
- Private Git backup of durable inputs plus an immediately usable graph/wiki
  snapshot, with deterministic rebuild after real concurrent input changes.
- Runtime logs, redacted support bundle, doctor checks, and optional deduplicated
  GitHub issue reporting.

This foundation already supports several local coding agents and a sequential
multi-device workflow. It does not yet provide conflict-free simultaneous
editing or a shared authenticated team service.

## Near-term contract cleanup

## Coding-agent integration matrix

Lorekeep integrates with **every** coding agent a developer uses, so the second
brain is fed from all sessions — not just the ones lorekeep happens to support
first.

| Agent | Vendor | MCP wired | Memory/session import | `lorekeep mcp add` | Status |
|---|---|:---:|:---|:---:|---|
| **Claude Code** | Anthropic | ✅ `.mcp.json` | ✅ `raw/claude-memory/` + `raw/claude-session/` | ✅ `--agent claude` | **Shipped** |
| **Codex CLI** | OpenAI | ✅ `config.toml` | ✅ `raw/codex-memory/` + `raw/codex-session/` | ✅ `--agent codex` | **Shipped** |
| **Cursor** | Anysphere | ✅ `~/.cursor/mcp.json` | ✅ SQLite → `raw/cursor-session/` (cloud-lazy-load caveat) | ✅ `--agent cursor` | **Shipped** |
| **opencode** | SST | ✅ `opencode.json` | ✅ SQLite → `raw/opencode-session/` | ✅ `--agent opencode` | **Shipped** |
| **Grok Build** | xAI | ✅ `~/.grok/config.toml` | ✅ JSONL → `raw/grok-session/` | ✅ `--agent grok` | **Shipped** |
| **Qoder** | Alibaba | ✅ `.mcp.json` / `settings.json` | ✅ hook transcript → `raw/qoder-session/` | ✅ `--agent qoder` | **Shipped** |
| **GitHub Copilot CLI** | GitHub | ✅ `mcp-config.json` | ✅ events JSONL → `raw/copilot-session/` | ✅ `--agent copilot` | **Shipped** |
| **Command Code** | Command Code | ✅ `mcp.json` | ✅ transcript JSONL → `raw/cmd-session/` | ✅ `--agent cmd` | **Shipped** |
| **Gemini CLI** | Google | 🚧 not detected | 🚧 not detected | 🚧 planned | **Planned** |
| **Aider** | OSS | 🚧 | 🚧 chat history → `raw/aider-session/` | 🚧 planned | **Planned** |
| **Windsurf** | Codeium | 🚧 | 🚧 | 🚧 planned | **Planned** |
| **Zed** | Zed Industries | 🚧 | 🚧 | 🚧 planned | **Planned** |
| **Cline** | OSS (VS Code) | 🚧 | 🚧 | 🚧 planned | **Planned** |
| **Continue** | OSS (VS Code/JetBrains) | 🚧 | 🚧 | 🚧 planned | **Planned** |
| **Amazon Q Developer** | AWS | 🚧 | 🚧 | 🚧 planned | **Planned** |
| **Goose** | Block | 🚧 | 🚧 | 🚧 planned | **Planned** |
| **OpenHands** | All Hands AI | 🚧 | 🚧 | 🚧 planned | **Planned** |

**Priority for new integrations:**

1. **Gemini CLI** — native MCP support; high adoption.
2. **Aider** — plain-text chat logs trivially map to `raw/aider-session/`.
3. **Windsurf / Zed** — both support MCP; need session-format research.
4. **Cline / Continue** — VS Code extension MCP; need session-format research.

## Near-term contract cleanup

Before expanding the surface, align a few existing interfaces whose behavior is
currently documented but awkward:

- normalize the `serve --transport http` CLI label with FastMCP's actual
  `streamable-http` value, or remove it until a supported server mode exists;
- make manual Codex quick-import default to `codex-memory`, matching registry
  automation;
- make manual and watcher resolve status transitions identical for accepted,
  flagged, and rejected proposals; and
- either implement `resolve --archive` or remove the currently inert option.

These are compatibility/clarity fixes, not a promise of remote authentication
or team hosting.

## Product directions

### 1. Multi-agent concurrency hardening

**Goal:** many local agents can propose and resolve knowledge concurrently
without lost writes, nondeterministic outcomes, or ambiguous attribution.

Planned work includes stress/fault tests across processes, clearer
contradiction policy, stronger agent/device identity provenance, and identical
manual/daemon resolution semantics. The process-safe append and global resolve
lock are already implemented; distributed locking is not.

### 2. Multi-device reconciliation

**Goal:** several devices can edit the same brain without relying on an operator
to untangle every conflict.

Today Git fetch/rebase/push is sequential. Planned work should reconcile
append-only journals safely, define raw-document conflict policy, report
unmerged states visibly, and preserve deterministic rebuild. Git can remain the
default transport; conflict-free simultaneous editing is not shipped.

### 3. Knowledge-source connectors

**Goal:** ingest useful knowledge already present in repositories and operating
systems.

Candidates include selected READMEs/ADRs/runbooks, service catalogs, CI state,
observability signals, Confluence, PDFs, and URLs. Each connector must be
read-only by default, provenance-preserving, incremental, namespace-explicit,
and testable without weakening file sovereignty. None is built in today.

### 4. Proactive curation

**Goal:** surface drift, duplicates, gaps, and contradictions without pretending
that the current watcher is an autonomous curator.

Current `agent lint`, `agent suggest`, and `agent contribution` are one-shot
commands; the watcher reacts to filesystem/session events. Planned work may add
scheduled review/digests and source-backed enrichment, with review gates for
uncertain or contradictory facts. Nightly/weekly jobs are not currently
implemented.

### 5. Authenticated team serving

**Goal:** host selected namespaces behind a shared endpoint with real
identity-to-scope mapping.

This requires a supported streamable-HTTP lifecycle, OIDC/SSO, authorization
tests, auditability, schema compatibility, and operational guidance. A local
stdio process plus `LOREKEEP_READ_NS` is not sufficient for multi-user trust.

### 6. Retrieval quality

**Goal:** improve recall and ranking as graphs grow while keeping the compact MCP
contract.

Candidates include hybrid keyword/vector ranking, better relation-aware
expansion, query guidance, and evaluation across temporal/multi-hop tasks. The
current implementation has SQLite FTS with scan fallback, scoped graph
traversal, a construction harness, and a LoCoMo-oriented retrieval harness.
End-to-end agent reasoning evaluation remains planned.

## Design constraints for roadmap work

- Keep durable knowledge inspectable and portable; derived indexes must remain
  rebuildable.
- Preserve deterministic compilation for unchanged inputs.
- Keep permission in `ScopedGraph`, never in ad hoc tool handlers.
- Prefer a small composable MCP surface over one tool per product feature.
- Distinguish implemented behavior, experimental developer commands, and
  planned work in both code and documentation.
- Add offline regression tests for every new contract; use real providers only
  in explicit smoke/evaluation runs.

## Related

- [Architecture overview](architecture/overview.md)
- [Agent/watcher](architecture/agent.md)
- [Permission model](architecture/permission.md)
- [Testing and evaluation](architecture/evaluation.md)
- [Serve guide](guides/serve.md)
