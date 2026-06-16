"""Shared loader for facts.jsonl -> list[Node|Edge]. Used by store + eval."""
from __future__ import annotations

import json
from pathlib import Path

from lorekeep.models import Edge, Node


def read_facts(path: Path) -> list[Node | Edge]:
    """Read a facts.jsonl file (one JSON object per line) into typed facts."""
    facts: list[Node | Edge] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        if d["kind"] == "node":
            facts.append(Node.model_validate(d))
        else:
            facts.append(Edge.model_validate(d))
    return facts
