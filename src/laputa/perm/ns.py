"""Namespace permission rules. Deny-by-default.

Node visible iff ns ∩ effective_ns ≠ ∅. Edge visible iff BOTH endpoints visible
AND edge.ns ∩ effective_ns ≠ ∅. effective_ns = allowed ∪ {public}.
"""
from __future__ import annotations

from laputa.models import Edge, Node


def effective_ns(allowed_ns) -> set[str]:
    return set(allowed_ns) | {"public"}


def is_node_visible(node: Node | None, eff_ns: set[str]) -> bool:
    if node is None:
        return False
    return bool(set(node.ns) & eff_ns)


def is_edge_visible(
    edge: Edge, from_node: Node | None, to_node: Node | None, eff_ns: set[str]
) -> bool:
    if not is_node_visible(from_node, eff_ns):
        return False
    if not is_node_visible(to_node, eff_ns):
        return False
    return bool(set(edge.ns) & eff_ns)


from laputa.store.graph import GraphStore


class ScopedGraph:
    """The single permission chokepoint: wraps a GraphStore and filters every query."""

    def __init__(self, graph: GraphStore, allowed_ns) -> None:
        self._g = graph
        self._allowed = set(allowed_ns)
        self._eff = effective_ns(allowed_ns)

    @property
    def allowed_namespaces(self) -> set[str]:
        return self._allowed

    def _node_visible(self, node: Node | None) -> bool:
        return is_node_visible(node, self._eff)

    def get_node(self, id: str) -> Node | None:
        node = self._g.get_node(id)
        return node if self._node_visible(node) else None

    def neighbors(self, id: str, edge_type: str | None = None, depth: int = 1) -> dict:
        start = self._g.get_node(id)
        if not self._node_visible(start):
            return {"nodes": [], "edges": []}
        raw = self._g.neighbors(id, edge_type, depth)
        visible_ids = {n.id for n in raw["nodes"] if self._node_visible(n)}
        visible_ids.add(id)
        nodes = [self._g.get_node(nid) for nid in sorted(visible_ids)]
        edges = [
            e for e in raw["edges"]
            if e.from_ in visible_ids and e.to in visible_ids and bool(set(e.ns) & self._eff)
        ]
        return {"nodes": nodes, "edges": edges}
