# Journal architecture

Journals isolate coding-agent writes from the served graph. MCP and
conversational ingest append proposals; resolve validates and publishes accepted
facts later. The read path consumes only `facts.jsonl`.

## Namespace routing

The active layout is namespace-partitioned:

```text
pending/
├── backend/journal.jsonl
├── frontend/journal.jsonl
└── me/journal.jsonl
```

Agent identity is stored on each entry; Lorekeep does not currently maintain a
separate agent-routed journal path. `load_journals` recursively reads every file
named `journal.jsonl`, so older/custom nested layouts remain discoverable.

MCP read scope and write ownership are independent. Write tools discard a
caller-provided fact namespace and use exactly one concrete
`config.namespaces.write` namespace (default `me`) for both `fact.ns` and the
journal path. `LOREKEEP_READ_NS` can restrict reads or contain wildcard patterns
without affecting journal ownership. Glob/comma-separated write namespaces are
rejected, so `namespaces.read: ["*"]` cannot create a literal `pending/*/`
directory.

## Entry contract

```json
{
  "fact": {
    "kind": "node",
    "id": "svc:checkout",
    "type": "service",
    "ns": ["backend"],
    "props": {"name": "Checkout"},
    "src": []
  },
  "agent": "codex",
  "device": "workstation",
  "entry_id": "f57d...",
  "ns": "backend",
  "confidence": 0.9,
  "proposed_at": "2026-08-08T08:00:00.000000Z",
  "status": "pending"
}
```

| Field | Source/use |
|---|---|
| `fact` | Proposed node/edge dictionary; operation/type checks occur at write and full Pydantic/schema checks at resolve |
| `agent` | `LOREKEEP_AGENT`, default `mcp`; `cli-ingest` for human-reviewed ingest |
| `device` | `LOREKEEP_DEVICE`, otherwise hostname |
| `entry_id` | Random collision-resistant status/update identity |
| `ns` | Server-enforced journal namespace |
| `confidence` | MCP value clamped into 0–1; human-approved ingest writes 1.0 |
| `proposed_at` | UTC microsecond timestamp; legacy fallback identity |
| `status` | Normally `pending`, `merged`, `flagged`, or `quarantined` |

## Append safety

`append_journal`:

1. resolves `pending/<ns>/journal.jsonl`;
2. serializes one sorted JSON line;
3. acquires a cross-process sibling lock (`fcntl` on POSIX, `msvcrt` on
   Windows);
4. opens in append mode, writes the complete line, flushes, and `fsync`s; and
5. releases the lock.

Concurrent local processes targeting the same namespace serialize writes.
Different namespaces naturally use different files/locks.

Full resolve also acquires `pending/.resolve.lock`, serializing the whole
graph-read → journal-merge → graph-write → status-update transaction. This
prevents two local resolvers from publishing competing snapshots.

## Write paths

### MCP `propose_change`

- `create`: a complete node or edge fact;
- `link`: visible `from_id`/`to_id`, schema edge type, optional props; and
- `update`: visible fact id plus a complete replacement props map.

`create` immediately checks kind/type and, for edges, visible/schema-compatible
endpoints; complete Node/Edge shape is deferred to resolve. `link` rejects
missing ids/type, hidden or invalid endpoints, and non-object props. `update`
rejects a missing/hidden id or missing/non-object replacement props. Resolve
then performs full Pydantic and schema validation for every journal entry.

### MCP `review_note`

`contradiction` requires exactly two fact ids; `improvement` requires a non-empty
description. Both create a `note` candidate with confidence `0.0`, intentionally
keeping it out of ordinary domain facts during resolve. The journal/manifest is
the curator review trail.

### `agent ingest`

This explicit provider-backed workflow extracts facts from one raw file, shows
them to the user, and journals approved candidates with confidence `1.0`. It is
not the same as automatic transcript capture or batch compile.

## Confidence gate

For pending entries:

| Range | Resolve result |
|---|---|
| `>= 0.8` | accepted and merged |
| `>= 0.5` and `< 0.8` | accepted, merged, and flagged for review |
| `< 0.5` | quarantined; not added as an ordinary fact |

Confidence is self-reported input, not verified probability. Resolve also
requires Pydantic shape, known schema types, and valid edge endpoints. Accepted
facts are stamped with agent/device/confidence/proposal metadata.

No per-minute/session rate limit, cross-namespace special review gate, or
automatic contradiction arbitration is implemented today. Those are potential
hardening work, not current security controls.

## Merge details

Entries are sorted deterministically; nodes are processed before edges so a node
and a link proposed in the same batch can resolve together. A new node id is
added; an existing node merges props/source/namespaces. Logical duplicate edges
share endpoints/type/validity and are coalesced.

An edge whose endpoint is absent or violates schema is quarantined immediately.
There is no retry counter/queue. Re-proposing after the endpoint exists creates a
new journal entry that a later pass can accept.

Accepted replay behaves differently from a newly pending merge: it prefers the
current compiled props so an old accepted journal line cannot overwrite fresh
raw extraction during a rebuild. Source/namespace/prose enrichment remains
deterministic.

## Status and retention

Status updates rewrite the namespace journal through a temp file + `os.replace`
under its lock. Processed lines stay in place for replay/audit, and later normal
passes skip non-pending entries. Startup/post-compile replay explicitly includes
accepted `merged`/`flagged` entries.

The public CLI currently exposes `resolve --archive`, but the implementation does
not branch on that option or move journal files. Treat journals as status-marked
and retained; do not rely on archive/truncate behavior until the runtime contract
is implemented and tested.

## Multi-device behavior

`pending/` journals are durable backup inputs. After Git sync, watcher startup
replays accepted entries so separately compiled devices converge on those facts.
Entry ids and deterministic ordering prevent many accidental duplicates, but Git
is still sequential transport:

- simultaneous appends to different namespace files usually merge cleanly;
- simultaneous edits/status rewrites to the same journal may conflict; and
- a human must preserve valid JSONL lines and entry ids during conflict repair.

Process locks protect one filesystem; they do not coordinate different devices.

## Privacy

Journals can hold source-derived facts, agent inferences, review notes, and
quarantined content. They are intentionally not included in public support
bundles, but they are committed by Lorekeep backup for replay. Use a private
remote with access controls appropriate to the most sensitive namespace.

## Related

- [Pipeline](pipeline.md)
- [Serve and MCP](serve-mcp.md)
- [Permission](permission.md)
- [Backup guide](../guides/backup.md)
