# Temporal model

Nodes and edges can carry `valid_from` and `valid_to` ISO dates. Lorekeep uses a
half-open interval:

```text
valid_from <= t < valid_to
```

Either boundary can be `null`, meaning unbounded on that side. A fact with
`valid_to: 2025-03-01` is no longer active on 2025-03-01; a replacement can
begin on that same day without overlap.

The model uses calendar dates, not timestamps or transaction time. `src` and
journal metadata record provenance, but they do not form a second bitemporal
axis.

## Query operations

MCP exposes all temporal reads through
`temporal_query(mode, params)`. `search(..., as_of=)` uses the same half-open
window as a **hit filter** on ranked search results (empty = today,
`as_of="all"` = no filter). It does not return the full graph at that date;
use `at_time` for a snapshot.

### Snapshot

```json
{"mode": "at_time", "params": {"time": "2025-03-01"}}
```

Returns active visible nodes and active visible edges. An edge is returned only
when both of its endpoints are also active and visible in the snapshot.

### History

```json
{"mode": "history", "params": {"id": "svc:payments-api"}}
```

Returns the visible node followed by visible incoming/outgoing edges, ordered by
`valid_from` with an unbounded start first. This is incident-edge history for
the current node id; it is not an audit log of every mutation to `props`.

### Changes

```json
{"mode": "changes", "params": {
  "from_time": "2025-01-01",
  "to_time": "2025-06-01"
}}
```

Returns two edge lists:

- `began`: `valid_from` falls in `[from_time, to_time)`;
- `ended`: `valid_to` falls in `[from_time, to_time)`.

The current operation reports relationship changes only; node start/end events
are not included.

Invalid/missing dates or modes return structured MCP errors. Internally the
store accepts parsed `date` values.

## Representing change

Lorekeep preserves fact validity windows rather than rewriting every historical
relationship into a single current value. For example, an old dependency ending
on 2025-03-01 and a replacement beginning that day are two edge facts. Snapshot
selects the applicable relationship; history shows both.

This is validity history, not full event sourcing: recompiling curated raw
documents rebuilds the derived graph, and ordinary prop changes are not
automatically retained as separate versions unless the source/extracted facts
model them that way.

## Permission composition

`ScopedGraph.snapshot`, `.history`, and `.changes` filter the pure
`GraphStore` result. `search` applies the same namespace rule, then the
`as_of` window when set. A hidden node, a hidden endpoint, or an edge outside the
effective namespace cannot leak through a temporal or search response. See
[Permission](permission.md).

## Related

- [Data model](data-model.md)
- [Compile pipeline](pipeline.md)
- [Serve and MCP](serve-mcp.md)
- [Testing and evaluation](evaluation.md)
