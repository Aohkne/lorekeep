"""Load gold + compiled facts, and define match keys for evaluation."""
from __future__ import annotations

import json
from pathlib import Path

from laputa.models import Edge, Node


def _read_facts(path: Path) -> list[Node | Edge]:
    facts: list[Node | Edge] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        if d["kind"] == "node":
            facts.append(Node.model_validate(d))
        else:
            facts.append(Edge.model_validate(d))
    return facts


def load_gold(gold_dir: Path) -> list[Node | Edge]:
    """Load every *.facts.jsonl under gold_dir."""
    facts: list[Node | Edge] = []
    for p in sorted(gold_dir.glob("**/*.facts.jsonl")):
        facts.extend(_read_facts(p))
    return facts


def load_compiled(graph_dir: Path) -> list[Node | Edge]:
    return _read_facts(graph_dir / "facts.jsonl")


def node_key(n: Node) -> tuple[str, str]:
    return (n.type, n.props.get("name", n.id))


def edge_key(e: Edge, nodes_by_id: dict[str, Node]) -> tuple[str, str, str]:
    f = nodes_by_id.get(e.from_)
    t = nodes_by_id.get(e.to)
    fn = f.props.get("name", e.from_) if f else e.from_
    tn = t.props.get("name", e.to) if t else e.to
    return (e.type, fn, tn)
