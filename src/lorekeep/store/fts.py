"""Optional FTS5 search cache over node text. Falls back to in-memory scan."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from lorekeep.models import Node


def node_text(n: Node) -> str:
    parts = [n.id, n.type]
    if "name" in n.props:
        parts.append(str(n.props["name"]))
    for k, v in n.props.items():
        if k != "name":
            parts.append(str(v))
    return " ".join(parts)


class FTSIndex:
    def __init__(self, db_path: Path) -> None:
        self.conn = sqlite3.connect(str(db_path))
        self.conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS nodes USING fts5(id, text)"
        )

    def build(self, nodes: list[Node]) -> None:
        self.conn.execute("DELETE FROM nodes")
        self.conn.executemany(
            "INSERT INTO nodes(id, text) VALUES(?, ?)",
            [(n.id, node_text(n)) for n in nodes],
        )
        self.conn.commit()

    def search(self, query: str, limit: int = 10) -> list[str]:
        terms = query.split()
        if not terms:
            return []
        # MCP search accepts plain user text, not FTS5 query syntax.  Quote
        # every whitespace-delimited term so punctuation in common entity IDs
        # (for example ``payments-api`` or ``svc:payments``) stays literal.
        literal_query = " AND ".join(
            '"' + term.replace('"', '""') + '"' for term in terms
        )
        cur = self.conn.execute(
            "SELECT id FROM nodes WHERE nodes MATCH ? LIMIT ?",
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
