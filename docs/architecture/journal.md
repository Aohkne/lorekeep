# Journal: agent-driven knowledge accumulation

The journal is the mechanism by which coding agents contribute knowledge to Lorekeep **at runtime, at zero marginal LLM cost**. Agents propose facts during conversation; the journal captures them as append-only JSONL; a periodic resolve pass merges validated facts into `facts.jsonl`.

## Why journals?

Karpathy's LLM Wiki insight: the agent's conversation output IS knowledge, and it shouldn't disappear into chat history. But writing directly to `facts.jsonl` from multiple concurrent agents would create write conflicts, and low-quality facts would pollute the graph.

The journal pattern solves this:

| Problem | Journal solution |
|---|---|
| Write conflicts (multi-agent) | Append-only, per-namespace or per-agent JSONL files — no contention |
| Low-quality facts | Confidence-gated at resolve time; low-confidence → quarantine, never enters graph |
| LLM cost per write | Zero — agent already ran LLM; propose is just formatting |
| Audit trail | Every entry has `agent`, `proposed_at`, `confidence` |
| Read path isolation | Facts only visible after resolve; read path serves only validated facts |

## Journal layout

```
pending/
├── backend/journal.jsonl       # facts proposed in "backend" namespace
├── frontend/journal.jsonl
├── claude/journal.jsonl         # facts proposed by Claude Code agent
└── codex/journal.jsonl
```

Journals are **partitioned by namespace or agent id** so:
- Two agents in different namespaces never write to the same file
- Resolve can load journals selectively (e.g., only `backend/` when scoped)
- Journals are git-committable — line-based diffs merge cleanly

## Journal entry format

```jsonl
{"fact":{...},"agent":"claude","ns":"backend","confidence":0.85,"proposed_at":"2026-06-20T10:30:00Z","status":"pending"}
```

See [data model](data-model.md#pending-journal-format) for the full schema.

## Lifecycle of a proposed fact

```
┌─────────────────────────────────────────────────────────────┐
│                    AGENT SESSION                             │
│  Agent discovers: "service checkout is written in Rust"      │
│  Agent calls: propose_fact({id:"svc:checkout", ...}, 0.85)  │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                    JOURNAL APPEND                            │
│  → pending/backend/journal.jsonl                            │
│  Status: "pending"                                           │
│  Atomic: write line + fsync                                  │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼  (trigger: N writes or T minutes)
┌─────────────────────────────────────────────────────────────┐
│                    RESOLVE                                   │
│  1. Load facts.jsonl + all pending journals                  │
│  2. Merge by priority (raw/ > import > agent-propose)       │
│  3. Gate by confidence:                                      │
│     ≥0.8 → auto-merge                                       │
│     0.5-0.8 → merge + flag for review                       │
│     <0.5 → quarantine (do not merge)                        │
│  4. Dedup, validate, sort                                    │
│  5. Write facts.jsonl (atomic os.replace)                   │
│  6. Update journal entry status: "merged" or "quarantined"  │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                    GRAPH VISIBLE                              │
│  MCP server lazy-reloads facts.jsonl                         │
│  Next search/get_node/neighbors returns the new fact         │
│  Provenance: "agent:claude:session-abc123"                   │
└─────────────────────────────────────────────────────────────┘
```

## Confidence model

Confidence is agent-estimated, not algorithmically computed. This is intentional: the agent has full conversation context and is best positioned to judge certainty. Resolve applies a simple threshold model:

### Auto-merge (confidence ≥ 0.8)

Agent made explicit claim with source citation. Examples:
- "The codebase shows `svc:checkout` is written in Rust and listens on port 8080"
- "Based on `docker-compose.yml`, checkout depends on payments and auth"

Behavior: merged into `facts.jsonl`. If `id` conflicts with existing fact from higher-priority source, agent props are merged (union), not replaced.

### Flag for review (0.5 ≤ confidence < 0.8)

Agent mentioned or implied without explicit source. Examples:
- "Based on the architecture discussion, checkout likely depends on inventory too"
- "I think the auth service was migrated from Go to Rust in Q1 2025"

Behavior: merged into `facts.jsonl`, but added to `manifest.review` for curator attention. The fact is visible to queries but flagged as potentially inaccurate.

### Quarantine (confidence < 0.5)

Agent speculation or hedging. Examples:
- "It might be the case that payments uses PostgreSQL"
- "I'm not sure, but I think..."

Behavior: **not merged** into `facts.jsonl`. Added to `manifest.quarantine` with reason. Curator can manually promote by re-submitting with higher confidence or via raw/ compile.

## Idempotent propose

Re-proposing the same fact (same agent, same id within a session) is idempotent: the journal entry is deduplicated by `(agent, fact_id, proposed_at)` within a time window. This prevents agents from flooding the journal with repeated proposals during multi-turn conversations.

## Journal cleanup

After resolve marks entries as "merged" or "quarantined", the journal can be:
- **Truncated**: remove processed entries, keep only pending
- **Archived**: move to `pending/.archive/` with timestamp
- **Left in place**: resolve skips non-pending entries on subsequent runs

Default: truncate. Archive mode available via `lorekeep resolve --archive`.

## Write tool cost analysis

| Operation | LLM calls | Why |
|---|---|---|
| Agent conversation (existing) | 1 (already running) | Agent runs LLM to answer user |
| `propose_fact` | 0 | Formats existing output into JSON |
| `link_facts` | 0 | Creates edge between known nodes |
| `flag_contradiction` | 0 | Metadata-only flag |
| Resolve (periodic) | 0 | Pure Python: dedup, merge, sort |
| **Total marginal cost** | **0** | |

The key insight: the agent **already paid** for the LLM call. Proposing a fact captures that output before it disappears. Every conversation becomes a knowledge source at no extra cost.

## Related

- [Pipeline](pipeline.md) — how resolve merges journals into the graph.
- [Agent](agent.md) — autonomous agent that triggers resolve, lint, and compile.
- [Serve & MCP](serve-mcp.md) — the write tools agents call.
