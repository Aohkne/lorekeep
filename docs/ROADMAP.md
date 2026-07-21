# Roadmap — Lorekeep as a second brain

> Direction, not commitments. No dates. Items move as the product + users learn.

## North star

Lorekeep is a **second brain for code**: one knowledge graph that aggregates what
you and your coding agents learn, across every device you use, the services you
build and operate, and your team — and that **grows itself proactively**, not just
compiles on edit. The payoff: every agent (and you) reasons over the same
complete, coherent, temporally-correct picture instead of re-reading scattered
docs or re-deriving context per session.

The second brain has four **touchpoints** that feed it and one **proactive core**
that keeps it healthy:

| Touchpoint | Feeds the brain from… |
|---|---|
| **Many coding agents (parallel)** | Claude Code, Cursor, Codex, opencode — all read + contribute concurrently |
| **Many devices (simultaneous)** | laptop, desktop, server — same brain, kept in sync |
| **The software you build/operate** | your microservices' ADRs, runbooks, READMEs, observability, CI |
| **Team sharing** | personal knowledge shared where it belongs (namespaces + permission) |
| **Proactive core** | lorekeep reconciles, dedups, fills gaps, surfaces contradictions on its own |

## Shipped (the foundation)

Everything below is implemented today and is what the roadmap builds on:

- **Subject-aware ontology v2** — work-context node types + cross-namespace edges;
  the `me` namespace is subject-centric (altitude rule: tokens → attributes), team
  namespaces are entity-centric. ScopedGraph gates visibility per namespace.
- **MCP, 9 read + 5 write tools** — agents read at query time and propose facts at
  runtime through journal-based write tools (zero marginal LLM cost).
- **Three write paths → one resolve** — `raw/` compile, agent propose, session
  import — converge into a pure-logic resolve step.
- **4-agent session import + hooks** — Claude / Cursor / Codex / opencode memories
  → `raw/`; session-end hooks auto-trigger.
- **Agent daemon** — `agent watch` auto-compiles on raw/ change, auto-resolves
  pending journals, delta-imports session memory.
- **Backup + sync unit** — `lorekeep backup` commits `raw/` + schema to a private
  git repo; `facts.jsonl` is the sync unit (git or S3).
- **Obsidian/Tolaria wiki** — flat markdown + relationship frontmatter; one vault,
  both apps.
- **Profile + contribution** — `profile` (edit your personal source in Obsidian/
  Tolaria) and `contribution` (what should you share with a team namespace?).
- **Resolve normalize** — auto-merge duplicate ids (case/separator variants),
  preserve diacritics.

## Phases

Each phase: **goal**, **why it matters**, **scope**, **non-goals**. Phases are
directions, not a sequence — several advance in parallel.

### 1. Multi-agent concurrency (hardening)

- **Goal:** many agents proposing facts in parallel never lose or corrupt data.
- **Why:** you run Claude Code + Cursor + Codex at once; they all write to the same
  brain.
- **Scope:** sharper contradiction detection across parallel proposals (beyond the
  current `flag_contradiction`); agent-identity provenance on every fact; atomic
  journal appends; deterministic merge under concurrent writes.
- **Non-goals:** a central server (that's phase 5); real-time co-editing UX.
- **Builds on:** append-only journal + resolve merge (shipped).

### 2. Multi-device sync

- **Goal:** laptop + desktop edit the same brain simultaneously without clobbering.
- **Why:** the second brain follows you across machines; sequential pull/push isn't
  enough when two devices edit concurrently.
- **Scope:** conflict resolver for simultaneous `facts.jsonl` edits (field-level
  merge, last-writer-vs-merge policy options); optional central sync server for
  always-on reconciliation.
- **Non-goals:** replacing git as the transport (git stays the default).
- **Builds on:** git backup (`pull --rebase` + push) + facts.jsonl as sync unit.
- **Honest gap:** today's backup is sequential — concurrent device edits can
  conflict and need manual resolve.

### 3. Software-source connectors

- **Goal:** lorekeep reads the software you operate, not just docs you hand-write.
- **Why:** your microservices already hold the truth (ADRs, runbooks, READMEs,
  service catalog, observability, CI). Ingesting them keeps the brain live.
- **Scope:** git-repo watch (auto-ingest ADRs/runbooks/READMEs from service repos
  into the right namespace); observability → facts (e.g. a Signoz MCP feed turning
  recurring incidents/services into facts); CI status edges. Connectors are
  read-only ingest, namespace-tagged.
- **Non-goals:** lorekeep doesn't run your services or own their repos; it mirrors
  their knowledge into the graph.
- **Builds on:** `raw/<ns>` ingest + ontology v2 service/project/decision types.

### 4. Proactive agent

- **Goal:** the brain grows and heals itself, not just compiles on edit.
- **Why:** a second brain that silently drifts is worthless; lorekeep should
  reconcile, dedup, fill gaps, and surface contradictions on a schedule.
- **Scope:** scheduled nightly reconcile (dedup, normalize, fill gaps driven by the
  `contribution` view, surface flagged contradictions for review, auto-enrich from
  imported sessions). A "what changed + what needs you" digest.
- **Non-goals:** autonomous fact creation without review (human stays in the loop
  for low-confidence / contradictory facts).
- **Builds on:** daemon auto-compile/resolve + `contribution` + `lint`/`suggest`
  hooks.

### 5. Team server

- **Goal:** share namespaces across an org without each person running their own
  serve.
- **Why:** team knowledge needs a shared, authenticated endpoint, not N local MCP
  servers.
- **Scope:** streamable-HTTP transport; OIDC/SSO; shared namespace hosting; schema
  evolution without breaking older clients.
- **Non-goals:** multi-tenant SaaS (single-org deployment first).
- **Builds on:** MCP server + ScopedGraph permission (deny-by-default).

### 6. Retrieval quality

- **Goal:** agents query the second brain well — not just keyword `search`.
- **Why:** as the graph grows, recall + ranking matter for the agent to actually
  *use* it.
- **Scope:** embeddings / hybrid (keyword + vector) search; graph-guided retrieval
  (search → get_node → neighbors); retrieval eval against the existing Tier-1/Tier-2
  harnesses.
- **Non-goals:** training models; the graph stays file-based.
- **Builds on:** FTS5 cache + graph BFS neighbors (shipped).

## How phases relate

```
touchpoints (feed)            proactive core (heal)         serve (use)
─────────────────             ────────────────────          ──────────
1 multi-agent concurrency ─┐
2 multi-device sync ───────┼──► 4 proactive agent ──► 5 team server ──► 6 retrieval
3 software connectors ─────┘         (dedup/gap/         (shared ns)      (agents
                                       contradict)                          query)
```

Phases 1-3 feed the brain; phase 4 keeps it healthy; phases 5-6 let agents and
teams use it well. Everything builds on the shipped foundation above.

## References

- [Architecture overview](architecture/overview.md) — compile/serve phases, write
  paths, the append-and-resolve model.
- [Agent](architecture/agent.md) — the daemon that phases 3-4 extend.
- [Permission model](architecture/permission.md) — namespaces + ScopedGraph, the
  basis for team sharing (phase 5).
- [Evaluation](architecture/evaluation.md) — the retrieval-quality bar (phase 6).
