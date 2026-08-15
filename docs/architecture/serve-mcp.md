# Serve and MCP architecture

`mcp_server.py` exposes one local graph through FastMCP. The eight tool functions
remain plain Python callables so diagnostics and tests exercise the same routing
without starting a transport.

## Configuration and load

`cli serve` resolves allowed namespaces from comma-separated
`LOREKEEP_READ_NS` or `config.namespaces.read`. Namespace patterns support
wildcards — `*` matches all
graph namespaces, `*-session` matches by glob, literal names are kept as-is.
Expansion happens in `_rebuild()` against the graph's actual namespaces, so
new namespaces from recompile are picked up on lazy reload. Default config:
`namespaces.read: ["*"]`. Write ownership is resolved separately from the
concrete `config.namespaces.write` value (default `me`). Then it calls:

```python
configure(
    graph_dir=...,
    allowed_ns=...,
    write_ns=...,
    schema_path=...,
    pending_dir=...,
)
```

`configure` stores paths/scope and immediately rebuilds runtime state:

1. require/load `facts.jsonl` into `GraphStore`;
2. load schema when supplied;
3. wrap the store in `ScopedGraph`;
4. load manifest when present; and
5. create/rebuild the local SQLite FTS index, falling back to graph scan on
   failure.

No permission logic lives in `GraphStore`; every public read uses `ScopedGraph`.

## Lazy reload

`_require()` compares the current `facts.jsonl` mtime with the loaded value on
every query. A change closes/rebuilds FTS and reloads graph/schema/manifest/scope
before returning. Atomic writer publication prevents a reload from seeing a
half-written JSONL file.

The process need not reconnect after compile/resolve. It does need restart after
scope, command, or code configuration changes.

## Exact tool surface

### `search(query, limit=10)`

Calls scoped search, using FTS when available. The scope layer over-fetches by
three times, filters hidden ids, and returns at most `limit` visible node ids.

### `get_node(id)`

Returns a visible node serialized with aliases, or the same error for absent and
out-of-scope ids. **Alias resolution:** if the requested id was absorbed into a
canonical entity during resolve (via `same_as` edges or `merged_ids` props),
`GraphStore.resolve_alias()` maps it to the canonical node, and the response
includes `_resolved_from_alias` set to the original id for transparency.

### `neighbors(id, edge_type="", depth=1)`

Clamps depth into 1–5 and traverses both directions. Results include only visible
nodes and edges whose endpoints remain within the visible id set.

### `temporal_query(mode, params=None)`

Dispatches:

- `at_time` with required `params.time` → scoped snapshot;
- `history` with required `params.id` → scoped node/incident-edge history; and
- `changes` with `params.from_time` + `params.to_time` → scoped began/ended edge
  sets.

Invalid/missing parameters return structured errors rather than raising through
MCP.

### `context(section="all", topic="")`

Returns `schema`, `namespaces`, and/or `status`. Status combines scoped graph
statistics/topic coverage with process-wide manifest metadata and aggregate
pending-entry count. Graph counts and namespace names are always scoped; the
underlying `GraphStore` is never exposed through `ScopedGraph`. The pending count is not namespace-filtered.

### `propose_change(operation, payload, confidence)`

Routes `create`, `link`, and `update` through operation-specific type,
endpoint, property-container, and scope checks, then namespace-enforced journal
append. Complete Pydantic/schema validation is repeated/deferred at resolve.
Update replaces the complete props map of the current visible fact; it is not a
patch operation.

### `merge_entities(from_id, to_id, reason="")`

Declares that two nodes are the same entity. Creates a `same_as` edge
(`from`=alias, `to`=canonical) in the pending journal at confidence 1.0.
On resolve, the alias node merges into the canonical node, and the canonical
node persists `props.merged_ids` so the decision survives future compiles.
Cross-type merges are blocked.

### `review_note(kind, description, fact_ids=None)`

Creates a low-confidence `note` journal entry for `improvement`, or
`contradiction` with exactly two fact ids. Resolve keeps review notes out of
ordinary domain facts through the low-confidence gate.

The compact surface and enum contracts are regression-tested. New query paths
must not call `GraphStore` directly or bypass this count/scope contract.

## Passive resources

The server publishes three JSON resources:

- `lorekeep://schema` — current ontology contract;
- `lorekeep://namespaces` — sorted effective scope including `public`; and
- `lorekeep://status` — scoped graph state plus process-wide compile/pending
  operational metadata.

Clients that surface resources avoid spending model action choices on static
context. `context` exposes the same information for clients that do not.

## Read permission

For effective set `allowed ∪ {public}`:

- node visibility requires namespace intersection;
- edge visibility requires its namespace intersection **and** both endpoints
  visible; and
- graph temporal/statistics/search/traversal results apply the same rule.

The server never reveals whether a hidden id exists. See
[Permission](permission.md) for the chokepoint contract.

Static schema plus aggregate manifest/pending operational metadata are outside
the fact filter. They reveal no fact/journal content, but the current pending
count is process-wide rather than namespace-scoped.

## Journal write enforcement

Read patterns and write ownership are independent. `_write_journal` requires
one concrete `write_ns`, overwrites `fact.ns` with only that namespace, routes
the file through the same namespace, clamps confidence, and stamps:

- `LOREKEEP_AGENT` or `mcp`;
- `LOREKEEP_DEVICE` or hostname;
- random `entry_id`; and
- UTC microsecond `proposed_at`.

The append is locked and fsynced. The response reports acceptance/status/id/ns
but does not imply graph visibility. Glob or comma-separated values are rejected
as write namespaces, so generated paths can never be `pending/*/`. Resolve must
publish the fact first.

## Write validation details

- Create node: kind and schema node type are checked immediately; remaining
  complete-fact shape is validated at resolve.
- Create edge: kind/type plus visible/schema-compatible endpoints are checked
  immediately; remaining complete-fact shape is validated at resolve.
- Link: required ids/type must be non-empty, endpoints must be visible and
  schema-compatible, and props must be an object.
- Update id must resolve to a visible node/edge and `props` must be an object.
- Caller `ns` is discarded.
- Review descriptions cannot be blank; contradictions require two ids.

These controls are implemented. Per-agent rate limits, special cross-namespace
review gates, and automatic contradiction resolution/arbitration are not.

## Transport

Coding-agent configs use FastMCP stdio and spawn a local process. Stdio blocks
waiting for its client by design.

The CLI currently labels a second value `http`, while the bundled FastMCP API
accepts `streamable-http` instead. Therefore `--transport http` currently raises
an unknown-transport error; it is not evidence that authenticated team hosting
is shipped. Aligning/validating that CLI value is separate runtime work. OIDC,
multi-user identity mapping, and hosted namespace authorization remain roadmap.

## Integration writers

`mcp add` and registry-backed `agent wire` share agent lookup and command
resolution. Writers know native config/hook formats and project/user locations
for Claude Code, Cursor, Codex, and opencode. Install source selects `uvx`, local,
or configured Git/path invocation without hardcoding a machine checkout into a
portable PyPI config.

The instruction snippet printed during onboarding tells agents to load status,
search, expand nodes/relations/time, cite `src`, and journal verified changes.
Availability alone does not force a client/model to query.

## Diagnostics

`lorekeep doctor` loads graph/schema, configures this server directly, and calls
`context("namespaces")`. It separately pings the provider when credentials are
available. `serve` also converts missing/incompatible MCP dependency and invalid
startup config into actionable errors and runtime events.

## Related

- [Serving guide](../guides/serve.md)
- [Journal](journal.md)
- [Permission](permission.md)
- [Temporal model](temporal.md)
- [Runtime logging](../guides/runtime-logging.md)
