# Architecture overview

Lorekeep treats raw Markdown, schema, and journals as durable knowledge inputs
and `facts.jsonl` as a deterministic compiled graph. Coding agents query the
graph through MCP and can append proposals to journals, but they never mutate
the served graph directly.

This document describes current implementation. Future connectors, scheduled
curation, conflict-free multi-device reconciliation, and authenticated team
hosting are in the [roadmap](../ROADMAP.md).

## Phase boundary

Lorekeep separates expensive/authoritative construction from lightweight scoped
querying:

```text
COMPILE / CURATE
  raw Markdown ──> chunk ──> schema-constrained LLM extraction ──> resolve
  journals ─────────────────> confidence gate + deterministic merge ─┘
                                                        │
                                                        v
                                           facts.jsonl + manifest
                                                        │
                                                        └──> wiki Markdown

SERVE / USE
  facts.jsonl ──> GraphStore ──> ScopedGraph ──> seven MCP tools
       ^                                             │
       └──── atomic resolve <──── journal append <───┘
```

Compile-time extraction is the only normal path that needs a dedicated LLM
request. Runtime reads, journal appends, resolve, wiki, lint, status, permission,
and temporal filtering are local deterministic operations. Manual deep session
import and `agent ingest` are explicit additional provider-using operations.

## Durable and derived state

| State | Role | Backup expectation |
|---|---|---|
| `raw/<ns>/*.md` | Human/agent-authored source | durable |
| `schema.json` | Ontology/extraction contract | durable |
| `pending/<ns>/journal.jsonl` | Proposed/accepted/review history | durable and private |
| `config.yaml` | Local provider, scope, automation settings | local; may contain secrets |
| `graph/facts.jsonl` | Sorted served graph | derived; backed-up snapshot |
| `graph/manifest.json` | Compile/resolve diagnostics | derived; backed-up snapshot |
| `wiki/` | Human-readable full-graph projection | derived; backed-up snapshot |
| `cache.json`, `fts.sqlite` | Extraction and search caches | derived/local |

The backup Git repository keeps durable source/schema/journal state plus the
latest graph/wiki read-through snapshot. Config/secrets, extraction/FTS caches,
logs, app-local wiki settings, and transient files remain ignored. Generated
snapshot paths are non-mergeable so Git cannot synthesize a graph from two
independent compile publications; a real concurrent update converges durable
inputs and recompiles once.

## Three knowledge-entry paths

### Raw compile

Handwritten docs and automatic transcript/memory captures live under
`raw/<ns>/`. Ingest produces chunks with line provenance; extraction emits
schema-constrained candidates; resolve normalizes ids/aliases and quarantines
invalid facts; the writer publishes sorted JSONL atomically.

### Structured agent proposal

`propose_change`, `review_note`, and conversational `agent ingest` append
namespace-enforced `JournalEntry` records. Resolve applies confidence/schema
gates, stamps agent/device/time provenance, and merges accepted entries. This
path uses no new LLM for MCP writes because the calling coding agent already
formed the proposal.

### Session import/capture

Hooks and the watcher convert agent memory/transcript sources into deterministic
raw Markdown without a second LLM. Manual `lorekeep import` can instead run a
provider-backed deep summary. Both feed the ordinary raw compile path.

## Serve layering

`GraphStore` owns pure graph behavior over `networkx.MultiDiGraph`: lookup,
search, traversal, statistics, snapshots, history, and changes. It knows nothing
about MCP or permission. It builds an alias-to-canonical map at load time from
every node's `merged_ids` props; `get_node`, `neighbors`, and `history` all
resolve alias IDs to their canonical entity before lookup.

`ScopedGraph` wraps it as the single permission chokepoint. Effective namespaces
are configured scope plus `public`; edges require their own namespace and both
endpoints to be visible. Every MCP read routes through this wrapper.

`mcp_server` owns protocol schemas, graph/schema/manifest/FTS loading, passive
resources, lazy mtime reload, and journal validation/routing. The public surface
is fixed at seven tools and three resources.

## Publication and reload safety

- `facts.jsonl` and `manifest.json` are written through sibling temp files and
  `os.replace`, so readers do not observe a partially truncated graph.
- Facts are sorted and JSON keys are stable; unchanged effective inputs produce
  byte-identical `facts.jsonl`.
- Wiki publication builds replacement pages separately, preserves vault-local
  settings/log, and rolls back attempted page changes on publish failure.
- The MCP server checks `facts.jsonl` mtime on each query and rebuilds graph,
  manifest, schema, and FTS state when it changes.
- Journal append and status rewrite use cross-process locks; full resolve holds a
  separate transaction lock around read→merge→write→status.

## Automation boundary

`agent watch` is a polling event loop, not a general scheduler. It currently
reacts to:

- raw file count/mtime and schema mtime;
- pending journal mtime;
- supported memory/transcript sources;
- newly detected clients requiring idempotent wiring;
- package version changes; and
- configured backup remote state.

After compile it can replay journals, run deterministic self-heal, regenerate
the wiki, and synchronize backup. `agent lint`, `suggest`, `status`, `profile`,
and `contribution` are one-shot commands. There is no nightly/weekly task queue
or autonomous schema evolution.

## Security boundaries

- Extraction providers see raw chunk text at compile/deep-import time. Use a
  local provider when raw content cannot leave the device.
- API keys live in environment variables or local gitignored config, never in
  generated facts/support bundles.
- Namespace permission controls MCP reads/writes; the local wiki is an unscoped
  full-graph view and must remain private.
- Agent confidence is an input to resolve, not proof. Curated `src`, manifest
  review/quarantine, and journal provenance remain necessary for audit.
- Git backup is private and sequential; it is transport, not an authorization or
  conflict-free collaboration layer.

## Current design decisions

| Decision | Reason |
|---|---|
| File-backed durable inputs | Inspectable, portable, Git-friendly ownership |
| Compile once, query locally | Avoid re-processing all knowledge per question |
| Append then resolve | Isolate concurrent proposals from the served graph |
| Namespace chokepoint | Prevent accidental query/edge leakage |
| Temporal facts | Preserve history rather than overwrite it |
| Stdio-first local MCP | Broad coding-agent support and minimal infrastructure |
| Compact tool surface | Lower tool-selection ambiguity; compose richer queries |
| Deterministic derived graph | Reviewable rebuilds and device parity |

## Related

- [Data model](data-model.md)
- [Pipeline](pipeline.md)
- [Journal](journal.md)
- [Autonomous agent](agent.md)
- [Permission](permission.md)
- [Temporal model](temporal.md)
- [Serve and MCP](serve-mcp.md)
