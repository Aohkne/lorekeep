"""Tier-2 retrieval/temporal eval: minimal harness (not full benchmark datasets).

Loads a fixture graph + a small JSON question set, runs the scoped query path,
and checks expected node ids / edge-type presence per question. Full
HotpotQA/CronQuestions adaptation is deferred (spec §16 Tier 2).
"""
from __future__ import annotations

import json
from pathlib import Path

from laputa.perm.ns import ScopedGraph
from laputa.store.graph import GraphStore, parse_date


def retrieval_report(graph_dir: Path, questions_path: Path, allowed_ns) -> dict:
    store = GraphStore.from_jsonl(Path(graph_dir) / "facts.jsonl")
    scoped = ScopedGraph(store, allowed_ns)
    questions = json.loads(Path(questions_path).read_text())

    total = len(questions)
    failures = []
    for q in questions:
        ok = _check(scoped, q)
        if not ok:
            failures.append(q["id"])
    return {"total": total, "passed": total - len(failures), "failures": failures}


def _check(scoped: ScopedGraph, q: dict) -> bool:
    kind = q["kind"]
    if kind == "multihop":
        res = scoped.neighbors(q["start"], depth=q.get("depth", 1))
        got = {n.id for n in res["nodes"]}
        return set(q["expect_node_ids"]).issubset(got)
    if kind == "temporal":
        _, edges = scoped.snapshot(parse_date(q["time"]))
        types = {e.type for e in edges}
        if "expect_edge_types_present" in q:
            if not set(q["expect_edge_types_present"]).issubset(types):
                return False
        if "expect_edge_types_absent" in q:
            if set(q["expect_edge_types_absent"]) & types:
                return False
        return True
    return False
