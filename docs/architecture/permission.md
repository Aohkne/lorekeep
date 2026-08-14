# Permission model

Lorekeep's MCP read boundary is deny-by-default and centralized in
`ScopedGraph` (`src/lorekeep/perm/ns.py`). `GraphStore` contains pure graph
operations; any externally reachable query path must pass through the scoped
wrapper.

## Namespace origin and runtime identity

Compile derives namespaces from the source layout: facts extracted from
`raw/<ns>/*.md` carry that namespace. A fact can carry more than one namespace,
and `public` is the shared namespace.

At serve time, allowed namespaces come from:

1. comma-separated `LOREKEEP_NS`, when set; otherwise
2. `ns.default` in the resolved `config.yaml`.

The default read scope is `ns.default: ["*"]`. Default agent wiring omits
`LOREKEEP_NS`, so every client reads this central config on its next MCP start
instead of baking a stale scope into native agent config. An explicit wiring
`--ns` writes a read-scope override into the client's native MCP configuration.
The current local stdio model therefore treats process configuration as caller
identity; it does not authenticate a remote human or service.

## Visibility rules

For configured set `A`, define the effective set as:

```text
A' = A ∪ {public}
```

- Node visible if `node.ns ∩ A'` is non-empty.
- Edge visible if `edge.ns ∩ A'` is non-empty **and both endpoint nodes are
  visible**.
- Empty/unknown configured scope sees `public` only.

The endpoint rule prevents relationship leakage. A caller cannot infer a hidden
neighbor merely because an otherwise-visible edge points to it. Missing and
out-of-scope ids intentionally produce the same MCP response.

## Read paths covered

`ScopedGraph` applies the same rule to:

- id lookup and keyword/FTS search;
- incoming/outgoing neighbor traversal;
- temporal snapshot, history, and changes;
- namespace/status/coverage statistics; and
- endpoint validation for MCP write proposals.

Temporal snapshots additionally require returned edge endpoints to be both
visible and active in that snapshot.

## Write scope

`propose_change` and `review_note` do not trust a caller-supplied namespace.
Read permission and write ownership are separate: writes use exactly one
concrete `config.ns.personal` namespace (default `me`) and overwrite proposal
namespaces before appending the journal. Wildcard/comma-separated write values
are rejected. A broad `*` read scope therefore never creates a literal `*`
namespace or `pending/*/journal.jsonl` path.

Writes are still subject to schema/shape checks, visible endpoint checks, and
the confidence gate described in [Journal](journal.md). Namespace permission is
not a substitute for validating content.

## Important boundaries

- The generated wiki is a local projection of the **full graph**, not a
  per-caller `ScopedGraph` view. Do not publish a wiki directory as though it
  were namespace-filtered.
- MCP graph counts, namespace names, and topic coverage are scoped. Static
  schema plus aggregate compile/pending operational metadata are process-wide;
  the pending count currently covers every journal and is not filtered per
  namespace.
- The backup repository contains durable knowledge and a full-graph graph/wiki
  snapshot from every tracked namespace. It is not caller-scoped. Keep it
  private and control Git access separately.
- `GraphStore` is intentionally unscoped for compiler, repair, evaluation, and
  local wiki work. It must not be exposed directly through a new MCP/HTTP path.
- OIDC/SSO, remote identity-to-namespace mapping, and a shared authenticated
  server are not implemented.

## Extension rule

Any new query must add a `ScopedGraph` method first and test hidden nodes,
hidden edge endpoints, `public`, temporal composition, and stats/metadata
leakage. Calling `GraphStore` directly from a public tool is a permission bug.

## Related

- [Serve and MCP](serve-mcp.md)
- [Temporal model](temporal.md)
- [Journal](journal.md)
- [Data home and backup](../guides/data-home.md)
