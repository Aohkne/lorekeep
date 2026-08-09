# Serving the graph to coding agents

Lorekeep serves the compiled graph through a small MCP contract. The normal
client integration uses stdio: each coding agent starts a local Lorekeep process
with a fixed namespace scope. Graph updates are picked up lazily without
restarting that process.

## Prerequisites

Build and validate a graph first:

```bash
lorekeep compile
lorekeep doctor
```

`serve` requires `graph/facts.jsonl`. It loads the schema, manifest, and local
FTS index alongside the graph.

## Wire a client

For one client:

```bash
lorekeep mcp add --agent claude --scope project --ns backend
```

For registry-based detection/wiring:

```bash
lorekeep agent detect
lorekeep agent wire --scope user --ns backend
lorekeep agent wire --agent codex --scope user --ns backend
```

Supported names are `claude`, `cursor`, `codex`, and `opencode`. Writers preserve
unrelated client configuration and are idempotent: an already-correct target is
reported as unchanged without touching its mtime.

With `install_source: pypi`, a project-scoped Claude config is conceptually:

```json
{
  "mcpServers": {
    "lorekeep": {
      "command": "uvx",
      "args": ["lorekeep", "serve", "--transport", "stdio"],
      "env": {"LOREKEEP_NS": "backend"}
    }
  }
}
```

The writer selects the actual command and target format from install source,
agent, and scope. Do not copy this Claude-specific JSON over another client's
native config. Restart the client after wiring or changing scope.

## Scope and permission

Serve-time scope comes from comma-separated `LOREKEEP_NS`; without that env var,
it uses `config.ns.default`. `public` is always added implicitly.

Permission is deny-by-default:

- a node is visible when one of its namespaces is effective;
- an edge is visible only when its namespace **and both endpoint nodes** are
  visible; and
- missing and out-of-scope return the same result, preventing existence leaks.

All graph-fact paths—including temporal queries, graph status counts, topic
coverage, FTS results, and traversal—go through `ScopedGraph`. Static schema and
aggregate compile/pending operational metadata are process-wide and contain no
fact payloads; the pending count is not filtered per namespace.

## MCP surface

The server exposes exactly seven tools.

### `search(query, limit=10)`

Returns visible node ids matching id/type/property text. Lorekeep builds a local
SQLite FTS index at load/reload; if FTS is unavailable it falls back to graph
scan. Search over-fetches before scope filtering, then applies `limit`.

### `get_node(id)`

Returns one visible node with canonical id, type, namespace, temporal window,
props, `src`, and agent provenance when present. Missing and hidden nodes return
`{"error": "not found or out of scope"}`.

### `neighbors(id, edge_type="", depth=1)`

Traverses incoming and outgoing visible edges. Depth is clamped to 1–5. An empty
`edge_type` means all types. The payload contains deduplicated nodes and edges;
permission filtering is applied at every returned endpoint.

### `temporal_query(mode, params)`

One tool composes the three temporal operations:

```json
{"mode": "at_time", "params": {"time": "2025-03-01"}}
{"mode": "history", "params": {"id": "svc:payments-api"}}
{"mode": "changes", "params": {
  "from_time": "2025-01-01", "to_time": "2025-06-01"
}}
```

Validity is half-open: `valid_from <= t < valid_to`; `null` is unbounded.

### `context(section="all", topic="")`

Returns all context, or one of `schema`, `namespaces`, or `status`. The graph
portion of status is namespace-filtered and contains visible node/edge counts by
type, namespaces, source-provenance counts, validity freshness, and topic
coverage. Compile-manifest metadata and the aggregate unresolved-journal count
are process-wide; the response contains no journal payload.

Passing a topic adds a lightweight visible-coverage probe over node ids, types,
and property values:

```json
{"section": "status", "topic": "payments"}
```

The `provenance.curator`/`agent` split currently uses presence of node `src` as
its signal. Treat it as a coverage hint, not a cryptographic trust decision.

### `propose_change(operation, payload, confidence)`

Appends a structured proposal to a namespace journal:

| Operation | Payload |
|---|---|
| `create` | complete node or edge fact |
| `link` | `from_id`, `to_id`, `edge_type`, optional `props` |
| `update` | visible fact `id` and the **complete replacement** `props` map |

Node/edge types are checked against the loaded schema. Edge endpoints must exist,
be visible, and satisfy allowed endpoint types. No proposal changes
`facts.jsonl` directly.

### `review_note(kind, description, fact_ids=None)`

Records an `improvement`, or a `contradiction` referencing exactly two fact ids.
Review notes use low confidence deliberately, so resolve retains them in the
review/quarantine trail rather than treating them as ordinary domain facts.

## Passive resources

Clients with MCP resource support can read the same contextual information
without adding action choices:

- `lorekeep://schema`
- `lorekeep://namespaces`
- `lorekeep://status`

`context` is the fallback for clients that do not expose resources to the model.

## Recommended agent instructions

Wiring only exposes tools. Whether the model calls them depends on the client and
its instructions. `init` and `mcp add` print a snippet; preserve an equivalent
rule in the relevant `CLAUDE.md`, `AGENTS.md`, Cursor rules, or opencode
instructions:

```markdown
Before answering architecture, code, domain, ownership, or historical questions,
call Lorekeep context(section="status") and search relevant terms. Follow matching
ids with get_node, then neighbors or temporal_query as needed. Cite src. If a fact
is missing, report weak coverage or possible namespace exclusion rather than
inventing it. Propose only source-backed facts and record contradictions for
review.
```

Good user prompts are explicit about using the graph and the desired time/scope:

- “Search Lorekeep for payment ownership and dependencies; cite provenance.”
- “Use Lorekeep history for ADR-17 and distinguish past from current state.”
- “Check Lorekeep status coverage before answering; say when evidence is weak.”

## Journal-based writes

Write tools derive their namespace from the server scope and overwrite any
caller-provided `ns`. With multiple non-public scopes, the fact carries all of
them and the journal is routed through the first configured namespace. Give a
write-capable agent the narrowest practical scope.

The flow is:

```text
MCP write
  → operation/type/endpoint/scope checks
  → process-locked append + fsync to pending/<ns>/journal.jsonl
  → resolve full Pydantic/schema validation + confidence gate
  → atomic facts.jsonl publication
  → MCP lazy reload on next query
```

Confidence behavior is `>=0.8` merge, `0.5..<0.8` merge + flag, and `<0.5`
quarantine. Agent/device/proposal metadata is stamped onto accepted facts.

Run `lorekeep resolve` manually, or let `agent watch` detect the journal mtime
change. Resolve uses no LLM call.

## Lazy reload

Each query stats `facts.jsonl`. When its mtime differs, the server rebuilds the
graph, schema/manifest state, and FTS index before answering. Therefore:

```bash
# raw changed
lorekeep compile

# only journals changed
lorekeep resolve
```

The next MCP query sees the new graph. Reconnect only when code, command, or
namespace configuration changed.

## Transport status

The supported coding-agent path is `--transport stdio`, which blocks while it
waits for an MCP client. A clean timeout is a useful boot smoke:

```bash
timeout 3 lorekeep serve --transport stdio </dev/null
```

Authenticated shared HTTP hosting is not shipped. The current CLI help retains
an `http` label, but the bundled FastMCP runtime expects the distinct
`streamable-http` transport name; until the CLI contract is corrected and
covered, do not treat `--transport http` as a working team-server mode.

## Related

- [Permission architecture](../architecture/permission.md)
- [Temporal architecture](../architecture/temporal.md)
- [Serve and MCP architecture](../architecture/serve-mcp.md)
- [Journal architecture](../architecture/journal.md)
- [Runtime logging](runtime-logging.md)
