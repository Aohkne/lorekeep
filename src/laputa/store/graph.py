"""GraphStore: load facts.jsonl into a networkx MultiDiGraph with temporal queries.

Pure graph logic. No permission, no MCP. Permission is applied by perm.ScopedGraph.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import networkx as nx

from laputa.facts_io import read_facts
from laputa.models import Edge, Node


def parse_date(value: str | None) -> date | None:
    if value is None or value == "":
        return None
    return date.fromisoformat(value)


class GraphStore:
    def __init__(self, nodes: list[Node], edges: list[Edge]) -> None:
        self._G = nx.MultiDiGraph()
        for n in nodes:
            self._G.add_node(n.id, node=n)
        for e in edges:
            self._G.add_edge(e.from_, e.to, key=e.id, edge=e)

    @classmethod
    def from_jsonl(cls, path: Path) -> "GraphStore":
        facts = read_facts(path)
        nodes = [f for f in facts if isinstance(f, Node)]
        edges = [f for f in facts if isinstance(f, Edge)]
        return cls(nodes, edges)

    def node_ids(self) -> set[str]:
        return set(self._G.nodes)

    def get_node(self, id: str) -> Node | None:
        if id not in self._G:
            return None
        return self._G.nodes[id]["node"]

    def all_nodes(self) -> list[Node]:
        return [d["node"] for _, d in self._G.nodes(data=True)]

    def all_edges(self) -> list[Edge]:
        return [d["edge"] for _, _, d in self._G.edges(data=True, keys=False)]
