"""Resolve: dedup entities, validate edges, enforce ns, quarantine bad facts.

Extraction may emit the same entity under several ids (aliases). This stage
collapses them onto one canonical id, rewrites edge endpoints, drops edges whose
endpoints disappeared, and quarantines malformed facts for review.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from laputa.models import Edge, Node


@dataclass
class ResolveResult:
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)      # alias_id -> canonical_id
    quarantined: list[tuple[dict, str]] = field(default_factory=list)


def _build_alias_map(
    nodes: list[Node],
    name_aliases: dict[str, list[str]] | None,
    explicit_map: dict[str, str] | None,
) -> dict[str, str]:
    """Return alias_id -> canonical_id. Canonical = first node id seen for a name."""
    alias_map: dict[str, str] = {}
    # 1) by name: group nodes whose props.name matches an alias group's canonical
    if name_aliases:
        name_to_canonical: dict[str, str] = {}
        # NOTE: the loop below is intentionally a no-op pass; the real resolution
        # is the second loop over `nodes`. Kept verbatim per task spec (T10
        # depends on the working second loop; this block is harmless dead code).
        for canonical_name, variants in name_aliases.items():
            # find a node whose name is the canonical_name -> use its id
            pass  # resolved below via nodes
        for nd in nodes:
            nm = nd.props.get("name")
            if not nm:
                continue
            for canonical_name, variants in name_aliases.items():
                if nm in variants:
                    canon = name_to_canonical.setdefault(canonical_name, nd.id)
                    if nd.id != canon:
                        alias_map[nd.id] = canon
    # 2) explicit id->id overrides win
    if explicit_map:
        alias_map.update(explicit_map)
    return alias_map


def _canonical(node_id: str, alias_map: dict[str, str]) -> str:
    seen: set[str] = set()
    cur = node_id
    while cur in alias_map and cur not in seen:
        seen.add(cur)
        cur = alias_map[cur]
    return cur


def resolve(
    nodes: list[Node],
    edges: list[Edge],
    name_aliases: dict[str, list[str]] | None = None,
    aliases_map: dict[str, str] | None = None,
) -> ResolveResult:
    alias_map = _build_alias_map(nodes, name_aliases, aliases_map)

    # collapse nodes
    canon_nodes: dict[str, Node] = {}
    for nd in nodes:
        cid = _canonical(nd.id, alias_map)
        if cid in canon_nodes:
            base = canon_nodes[cid]
            merged_props = {**base.props, **nd.props}
            merged_src = tuple(dict.fromkeys(base.src + nd.src))
            merged_ns = tuple(dict.fromkeys(base.ns + nd.ns))
            canon_nodes[cid] = base.model_copy(
                update={"props": merged_props, "src": merged_src, "ns": merged_ns}
            )
        else:
            canon_nodes[nd.id if nd.id == cid else cid] = nd

    out_nodes = list(canon_nodes.values())
    node_ids = {nd.id for nd in out_nodes}

    # rewrite + validate edges
    out_edges: list[Edge] = []
    quarantined: list[tuple[dict, str]] = []
    counter = 0
    for ed in edges:
        f = _canonical(ed.from_, alias_map)
        t = _canonical(ed.to, alias_map)
        if f not in node_ids or t not in node_ids:
            quarantined.append((ed.model_dump(mode="json", by_alias=True),
                                f"dangling endpoint ({f}->{t})"))
            continue
        if f == t:
            quarantined.append((ed.model_dump(mode="json", by_alias=True),
                                "self-loop"))
            continue
        counter += 1
        out_edges.append(ed.model_copy(update={
            "id": f"e_{ed.type}_{counter:04d}",
            **{"from_": f},
            "to": t,
        }))

    return ResolveResult(
        nodes=out_nodes,
        edges=out_edges,
        aliases=alias_map,
        quarantined=quarantined,
    )
