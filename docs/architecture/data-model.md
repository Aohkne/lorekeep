# Data model

The shared compile/serve contract is defined by strict Pydantic models in
`src/lorekeep/models.py`. Facts and schema definitions are frozen, and unknown
node/edge/schema fields are rejected. JSON serialization uses sorted keys and
the edge field alias `"from"`.

## Resolved data-home layout

```text
<data-home>/
├── raw/<namespace>/*.md
├── schema.json
├── pending/<namespace>/journal.jsonl
├── graph/
│   ├── facts.jsonl
│   ├── manifest.json
│   └── fts.sqlite
├── wiki/
├── logs/
├── cache.json
└── config.yaml                 # may hold an API key — never commit
```

Raw/schema/journals are durable knowledge inputs. Facts, manifest, FTS, wiki,
and cache are derived. Config is device-local and may contain credentials.

## Facts

`facts.jsonl` contains one `Node` or `Edge` per line.

### Node

```json
{
  "kind": "node",
  "id": "svc:payments-api",
  "type": "service",
  "ns": ["backend"],
  "valid_from": "2024-01-15",
  "valid_to": null,
  "props": {
    "name": "Payments API",
    "summary": "Accepts and coordinates payment requests.",
    "description": "Validates requests before delegating ledger operations."
  },
  "src": ["raw/backend/payments.md:12"],
  "provenance": null
}
```

### Edge

```json
{
  "kind": "edge",
  "id": "e_depends_on_0001",
  "type": "depends_on",
  "from": "svc:payments-api",
  "to": "svc:auth",
  "ns": ["backend"],
  "valid_from": "2024-01-15",
  "valid_to": null,
  "props": {
    "description": "Uses Auth to validate the caller before capture."
  },
  "src": ["raw/backend/payments.md:20"],
  "provenance": null
}
```

The Python field is `Edge.from_`; serialized JSON must use its alias `from` via
`by_alias=True`.

### Common fact fields

| Field | Meaning |
|---|---|
| `id` | Canonical identifier; unique per fact kind in the loaded graph |
| `type` | Node/edge type declared by schema |
| `ns` | One or more namespaces; represented as an immutable tuple in Python |
| `valid_from` | Inclusive ISO date, or unbounded when `null` |
| `valid_to` | Exclusive ISO date, or still current/unbounded when `null` |
| `props` | Schema-guided domain attributes; arbitrary JSON values remain loadable |
| `src` | Stable raw `path:line` provenance; empty for some agent facts |
| `provenance` | Optional agent/device/confidence/proposal metadata stamped at merge |

Stock schema v5 asks extraction for node `summary`/`description` and edge
`description` through common props, but those remain optional in the storage
model so older and custom graphs can load.

## Schema

`Schema` contains:

- integer `version`;
- `node_types`, each with prop type hints plus optional human `label`, `plural`,
  `display_prop`, and `id_prefix` (canonical slug prefix, e.g. `svc` for
  `service`, `prj` for `project` — enforced deterministically by
  `parse_response`);
- `edge_types`, each with allowed `from`/`to` node types, prop hints, forward
  `label`, and `inverse_label`; and
- optional `common_node_props` and `common_edge_props`.

Schema is both extraction guidance and a runtime validation contract. Candidate
types/endpoints are validated during resolve, and MCP create/link validates
against the loaded schema before journaling.

Naming note: the project historically called the subject-aware topology
“ontology v2.” The current stock file is **schema version 5**, adding
`visual_desc`/`image_links` media props to the photographable node types
(`person`, `service`, `project`, `team`, `document`) on top of the version 4
human-readable metadata/content contract. Use schema version numbers in
migration instructions to avoid ambiguity.

## Manifest

`graph/manifest.json` describes the latest compile or resolve publication:

| Group | Fields |
|---|---|
| Identity | `schema_version`, deterministic `run_id`, `facts_hash`, `compiled_at` |
| Size | `chunk_count`, `node_count`, `edge_count` |
| Incremental map | `chunk_hashes` |
| Failure/review | `errors`, `quarantine`, `review` |
| Journal counters | `merged_count`, `quarantined_count`, `flagged_count` |
| Human readability | `content_quality` coverage/duplication metrics |

`compiled_at` is operational time metadata and changes between compiles;
determinism applies to the sorted facts for unchanged effective input.

## Journal entry

```json
{
  "fact": {
    "kind": "node",
    "id": "svc:checkout",
    "type": "service",
    "ns": ["backend"],
    "props": {"name": "Checkout"}
  },
  "agent": "codex",
  "device": "workstation",
  "entry_id": "5c1d...",
  "ns": "backend",
  "confidence": 0.9,
  "proposed_at": "2026-08-08T08:00:00.000000Z",
  "status": "pending"
}
```

`entry_id` is collision-resistant and the preferred status-update key;
`proposed_at` is the backward-compatible fallback. Normal lifecycle values are
`pending`, `merged`, `flagged`, and `quarantined`, although the model currently
stores status as a string for compatibility.

The journal path is namespace-routed: `pending/<entry.ns>/journal.jsonl`. Agent
identity is an entry field, not a second active routing scheme.

## Document chunks and cache identity

`DocChunk` carries source path, one-based start/end lines, text, and namespace.
Its local hash covers path + chunk text. Extraction adds schema/prompt/model
fingerprints to the cache identity, so a semantic compiler-contract change
invalidates old output even when Markdown is unchanged.

## Component ownership

| Component | Owns |
|---|---|
| `compile/ingest.py` | Markdown → `DocChunk` |
| `compile/extract.py` | provider request, cache, candidate JSON |
| `compile/resolve.py` | normalization, alias merge, validation, journal merge |
| `compile/writer.py` | sorted facts and atomic graph/manifest publication |
| `journal.py` | locked append, recursive load, atomic status rewrite, resolve lock |
| `store/graph.py` | pure graph/temporal/search/statistics behavior |
| `perm/ns.py` | all namespace visibility decisions |
| `mcp_server.py` | protocol contract, lazy reload, FTS, journal input validation |
| `wiki.py` | deterministic full-graph human projection |

## Related

- [Pipeline](pipeline.md)
- [Journal](journal.md)
- [Permission](permission.md)
- [Temporal model](temporal.md)
