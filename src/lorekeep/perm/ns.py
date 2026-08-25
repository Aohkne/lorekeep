"""Namespace permission rules. Deny-by-default.

Node visible iff ns ∩ effective_ns ≠ ∅. Edge visible iff BOTH endpoints visible
AND edge.ns ∩ effective_ns ≠ ∅. effective_ns = allowed ∪ {public}.

Wildcard patterns (`*`, `*-session`) are expanded against the graph's actual
namespaces by :func:`expand_namespaces` before ScopedGraph is built.
"""
from __future__ import annotations

from fnmatch import fnmatchcase

from lorekeep.models import Edge, Node


def expand_namespaces(allowed_ns, graph_ns) -> set[str]:
    """Expand wildcard patterns against actual graph namespaces.

    ``"*"``          → all graph namespaces
    ``"*-session"``  → glob match (e.g. claude-session, codex-session)
    ``"me"``         → literal (kept even if not yet in graph)
    """
    result: set[str] = set()
    for pattern in allowed_ns:
        if "*" in pattern:
            result.update(fn for fn in graph_ns if fnmatchcase(fn, pattern))
        else:
            result.add(pattern)
    return result


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


from lorekeep.store.graph import GraphStore


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

    def get_edge(self, id: str) -> Edge | None:
        edge = self._g.get_edge(id)
        if edge is None:
            return None
        from_node = self._g.get_node(edge.from_)
        to_node = self._g.get_node(edge.to)
        return edge if is_edge_visible(edge, from_node, to_node, self._eff) else None

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

    def snapshot(self, time) -> tuple[list[Node], list[Edge]]:
        nodes, edges = self._g.snapshot(time)
        vis_nodes = [n for n in nodes if self._node_visible(n)]
        vis_ids = {n.id for n in vis_nodes}
        vis_edges = [
            e for e in edges
            if e.from_ in vis_ids and e.to in vis_ids
            and bool(set(e.ns) & self._eff)
        ]
        return vis_nodes, vis_edges

    def history(self, id: str) -> list[dict]:
        if not self._node_visible(self._g.get_node(id)):
            return []
        items = self._g.history(id)
        out: list[dict] = []
        for it in items:
            if it["kind"] == "node":
                out.append(it)
            else:
                f = self._g.get_node(it["from"])
                t = self._g.get_node(it["to"])
                if is_edge_visible(_edge_from_dict(it), f, t, self._eff):
                    out.append(it)
        return out

    def changes(self, from_t, to_t) -> dict:
        rep = self._g.changes(from_t, to_t)
        result = {"began": [], "ended": []}
        for key in ("began", "ended"):
            for ed in rep[key]:
                f = self._g.get_node(ed["from"])
                t = self._g.get_node(ed["to"])
                if is_edge_visible(_edge_from_dict(ed), f, t, self._eff):
                    result[key].append(ed)
        return result

    def list_namespaces(self) -> list[str]:
        return sorted(self._eff)

    def distances_from(
        self, center: str | None, *, as_of=None, cap: int = 4,
    ) -> dict[str, int]:
        """Undirected hop distances over visible (and optionally active) edges."""
        from lorekeep.store.rank import HOP_CAP, is_active

        if not center:
            return {}
        start = self.get_node(center)
        if start is None:
            return {}
        if as_of is not None and not is_active(start.valid_from, start.valid_to, as_of):
            return {}
        hop_cap = cap if cap > 0 else HOP_CAP
        dist = {start.id: 0}
        frontier = [start.id]
        depth = 0
        while frontier and depth < hop_cap:
            nxt: list[str] = []
            depth += 1
            for uid in frontier:
                for edge in self._g.out_edges(uid) + self._g.in_edges(uid):
                    if self.get_edge(edge.id) is None:
                        continue
                    if as_of is not None and not is_active(
                        edge.valid_from, edge.valid_to, as_of,
                    ):
                        continue
                    other = edge.to if edge.from_ == uid else edge.from_
                    if other not in dist and self.get_node(other) is not None:
                        dist[other] = depth
                        nxt.append(other)
            frontier = nxt
        return dist

    def search(
        self, query: str, limit: int = 10, fts=None, *,
        center_id: str | None = None, as_of=None,
    ) -> list[str]:
        from lorekeep.store.rank import filter_active_nodes, rank_nodes

        ids = self._g.search(query, limit * 8, fts)
        dist = self.distances_from(center_id, as_of=as_of)
        visible: list[Node] = []
        for nid in ids:
            node = self._g.get_node(nid)
            if node is None or not self._node_visible(node):
                continue
            visible.append(node)
        visible = filter_active_nodes(visible, as_of)
        return rank_nodes([node.id for node in visible], dist)[:limit]

    def search_facts(
        self, query: str, limit: int = 10, fts=None, *,
        center_id: str | None = None, as_of=None,
    ) -> list[Edge]:
        """Visible relationship facts matching ``query``, ranked."""
        from lorekeep.store.rank import filter_active_edges, rank_facts

        hits = self._g.search_facts(query, limit * 8, fts)
        visible = [edge for edge in hits if self.get_edge(edge.id) is not None]
        visible = filter_active_edges(visible, as_of)
        dist = self.distances_from(center_id, as_of=as_of)
        return rank_facts(visible, dist)[:limit]

    def typed_hops(self, edge: Edge, *, as_of=None, limit: int = 4) -> list[Edge]:
        """Scoped semantic 1-hop facts around ``edge`` endpoints."""
        from lorekeep.store.rank import HOP_CAP, typed_hops

        cap = limit if limit > 0 else HOP_CAP
        allowed = {
            e.id for e in (
                self._g.out_edges(edge.from_) + self._g.in_edges(edge.from_)
                + self._g.out_edges(edge.to) + self._g.in_edges(edge.to)
            )
            if self.get_edge(e.id) is not None
        }
        return typed_hops(
            self._g, edge, as_of=as_of, limit=cap, visible=allowed,
        )

    def stats(self, topic: str = "") -> dict:
        """Namespace-filtered graph statistics.

        If ``topic`` is given, also returns coverage info: how many visible
        nodes/edges match the topic string (case-insensitive on id, type,
        and prop values).
        """
        from datetime import date as date_t

        nodes = [n for n in self._g.all_nodes() if self._node_visible(n)]
        vis_ids = {n.id for n in nodes}
        edges = [
            e for e in self._g.all_edges()
            if e.from_ in vis_ids and e.to in vis_ids and bool(set(e.ns) & self._eff)
        ]
        node_types: dict[str, int] = {}
        edge_types: dict[str, int] = {}
        all_ns: set[str] = set()
        curator = 0
        agent = 0
        for n in nodes:
            node_types[n.type] = node_types.get(n.type, 0) + 1
            all_ns.update(n.ns)
            if n.src:
                curator += 1
            else:
                agent += 1
        for e in edges:
            edge_types[e.type] = edge_types.get(e.type, 0) + 1
            all_ns.update(e.ns)
        today = date_t.today()
        valid_tos = [n.valid_to for n in nodes if n.valid_to]
        valid_froms = [n.valid_from for n in nodes if n.valid_from]
        result = {
            "nodes": len(nodes),
            "edges": len(edges),
            "node_types": dict(sorted(node_types.items())),
            "edge_types": dict(sorted(edge_types.items())),
            "namespaces": sorted(all_ns),
            "provenance": {"curator": curator, "agent": agent},
            "freshness": {
                "oldest": min(valid_froms).isoformat() if valid_froms else None,
                "newest": max(valid_froms).isoformat() if valid_froms else None,
                "expired": sum(1 for t in valid_tos if t <= today),
            },
        }
        if topic:
            tlow = topic.lower()
            matching = [
                n for n in nodes
                if tlow in n.id.lower()
                or tlow in n.type.lower()
                or any(tlow in str(v).lower() for v in n.props.values())
            ]
            matching_types: dict[str, int] = {}
            for n in matching:
                matching_types[n.type] = matching_types.get(n.type, 0) + 1
            result["coverage"] = {
                "topic": topic,
                "matching_nodes": len(matching),
                "matching_types": dict(sorted(matching_types.items())),
                "node_ids": sorted(n.id for n in matching)[:20],
            }
        return result


def _edge_from_dict(d: dict) -> Edge:
    return Edge.model_validate(d)
