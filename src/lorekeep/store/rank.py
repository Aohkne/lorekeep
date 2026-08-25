"""Rank and pack search hits without an LLM.

FTS order is the lexical signal. Type weights prefer specific relationships
over ``relates_to``. Distance-to-center (undirected hops) is Graphiti-style
node-distance rerank. Typed 1-hop packing gives local overview without BFS-2
or community summaries.
"""
from __future__ import annotations

from datetime import date

from lorekeep.defaults import DEFAULT_SCHEMA
from lorekeep.models import Edge, Node
from lorekeep.store.graph import GraphStore

# Stock schema types kept on the retrieve/pack path. Generic and identity
# edges stay searchable but are demoted and never packed as neighbors.
DEMOTE_EDGE_TYPES = frozenset({"relates_to"})
IDENTITY_EDGE_TYPES = frozenset({"same_as"})
SEMANTIC_EDGE_TYPES = (
    frozenset(DEFAULT_SCHEMA["edge_types"]) - DEMOTE_EDGE_TYPES - IDENTITY_EDGE_TYPES
)
HOP_CAP = 4


def type_weight(edge_type: str) -> float:
    if edge_type in DEMOTE_EDGE_TYPES:
        return 0.15
    if edge_type in IDENTITY_EDGE_TYPES:
        return 0.2
    if edge_type in SEMANTIC_EDGE_TYPES:
        return 1.0
    return 0.5


def _edge_distance(edge: Edge, dist: dict[str, int]) -> int | None:
    if not dist:
        return None
    options = [dist[n] for n in (edge.from_, edge.to) if n in dist]
    return min(options) if options else None


def fact_score(fts_index: int, edge: Edge, dist: int | None) -> float:
    """Higher is better. FTS order, type, and graph distance to center."""
    rrf = 1.0 / (10 + fts_index)
    near = 1.0 / (1 + dist) if dist is not None else 0.45
    return rrf * (0.5 + type_weight(edge.type)) * (0.5 + near)


def rank_facts(edges: list[Edge], dist: dict[str, int]) -> list[Edge]:
    scored = [
        (fact_score(i, edge, _edge_distance(edge, dist)), i, edge)
        for i, edge in enumerate(edges)
    ]
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [edge for _score, _i, edge in scored]


def rank_nodes(ids: list[str], dist: dict[str, int]) -> list[str]:
    def key(item: tuple[int, str]) -> tuple[int, int]:
        i, nid = item
        d = dist.get(nid, 99)
        return (d, i)
    return [nid for _i, nid in sorted(enumerate(ids), key=key)]


def is_active(valid_from: date | None, valid_to: date | None, at: date) -> bool:
    return GraphStore._active(valid_from, valid_to, at)


def typed_hops(
    store: GraphStore,
    seed: Edge,
    *,
    as_of: date | None = None,
    limit: int = HOP_CAP,
    visible: set[str] | None = None,
) -> list[Edge]:
    """Semantic 1-hop edges touching either endpoint of ``seed``.

    ``visible`` if given is the set of edge ids allowed by the caller (scope).
    ``relates_to`` / ``same_as`` are never packed. Nested hops are not expanded.
    """
    seen = {seed.id}
    candidates: list[Edge] = []
    for nid in (seed.from_, seed.to):
        for edge in store.out_edges(nid) + store.in_edges(nid):
            if edge.id in seen:
                continue
            if edge.type not in SEMANTIC_EDGE_TYPES:
                continue
            if visible is not None and edge.id not in visible:
                continue
            if as_of is not None and not is_active(edge.valid_from, edge.valid_to, as_of):
                continue
            seen.add(edge.id)
            candidates.append(edge)
    candidates.sort(key=lambda e: (-type_weight(e.type), e.id))
    return candidates[:limit]


def filter_active_edges(edges: list[Edge], as_of: date | None) -> list[Edge]:
    if as_of is None:
        return edges
    return [e for e in edges if is_active(e.valid_from, e.valid_to, as_of)]


def filter_active_nodes(nodes: list[Node], as_of: date | None) -> list[Node]:
    if as_of is None:
        return nodes
    return [n for n in nodes if is_active(n.valid_from, n.valid_to, as_of)]
