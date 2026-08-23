"""Optional FTS5 search cache over node and edge text. Falls back to scan."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from lorekeep.models import Edge, Node


def node_text(n: Node) -> str:
    parts = [n.id, n.type]
    if "name" in n.props:
        parts.append(str(n.props["name"]))
    for k, v in n.props.items():
        if k != "name":
            parts.append(str(v))
    return " ".join(parts)


def edge_text(edge: Edge, endpoint_names: dict[str, str] | None = None) -> str:
    """Indexable fact sentence: type, endpoints, labels, and props."""
    parts = [edge.id, edge.type, edge.from_, edge.to]
    if endpoint_names:
        for nid in (edge.from_, edge.to):
            name = endpoint_names.get(nid)
            if name:
                parts.append(name)
    for _key, value in edge.props.items():
        parts.append(str(value))
    return " ".join(parts)


def _literal_match_query(query: str) -> str | None:
    """MCP search accepts plain user text, not FTS5 query syntax."""
    terms = query.split()
    if not terms:
        return None
    # Quote every whitespace-delimited term so punctuation in common entity
    # IDs (for example ``payments-api`` or ``svc:payments``) stays literal.
    return " AND ".join('"' + term.replace('"', '""') + '"' for term in terms)


def endpoint_names(nodes: list[Node]) -> dict[str, str]:
    names: dict[str, str] = {}
    for node in nodes:
        label = node.props.get("name") or node.props.get("title")
        if isinstance(label, str) and label.strip():
            names[node.id] = label
    return names


class FTSIndex:
    def __init__(self, db_path: Path) -> None:
        self.conn = sqlite3.connect(str(db_path))
        self.conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS nodes USING fts5(id, text)"
        )
        self.conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS edges USING fts5(id, text)"
        )

    def build(self, nodes: list[Node], edges: list[Edge] | None = None) -> None:
        names = endpoint_names(nodes)
        self.conn.execute("DELETE FROM nodes")
        self.conn.executemany(
            "INSERT INTO nodes(id, text) VALUES(?, ?)",
            [(n.id, node_text(n)) for n in nodes],
        )
        self.conn.execute("DELETE FROM edges")
        self.conn.executemany(
            "INSERT INTO edges(id, text) VALUES(?, ?)",
            [(e.id, edge_text(e, names)) for e in (edges or [])],
        )
        self.conn.commit()

    def search(self, query: str, limit: int = 10) -> list[str]:
        """Node ids matching ``query``. Kept as the entity-catalog search."""
        return self.search_nodes(query, limit)

    def search_nodes(self, query: str, limit: int = 10) -> list[str]:
        return self._match("nodes", query, limit)

    def search_edges(self, query: str, limit: int = 10) -> list[str]:
        return self._match("edges", query, limit)

    def _match(self, table: str, query: str, limit: int) -> list[str]:
        literal_query = _literal_match_query(query)
        if literal_query is None:
            return []
        cur = self.conn.execute(
            f"SELECT id FROM {table} WHERE {table} MATCH ? LIMIT ?",
            (literal_query, limit),
        )
        return [row[0] for row in cur.fetchall()]

    def close(self) -> None:
        self.conn.close()


def scan_search(nodes: list[Node], query: str, limit: int = 10) -> list[str]:
    q = query.lower()
    out: list[str] = []
    for n in nodes:
        if q in node_text(n).lower():
            out.append(n.id)
            if len(out) >= limit:
                break
    return out


def scan_search_edges(
    edges: list[Edge],
    query: str,
    limit: int = 10,
    endpoint_names: dict[str, str] | None = None,
) -> list[Edge]:
    q = query.lower()
    out: list[Edge] = []
    for edge in edges:
        if q in edge_text(edge, endpoint_names).lower():
            out.append(edge)
            if len(out) >= limit:
                break
    return out
