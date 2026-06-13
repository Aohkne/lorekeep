"""Tier-1 construction-quality evaluation vs a gold corpus."""
from __future__ import annotations

from pathlib import Path

from laputa.eval.gold import edge_key, load_compiled, load_gold, node_key
from laputa.models import Edge, Node


def precision_recall_f1(gold: set, got: set) -> tuple[float, float, float]:
    if not gold and not got:
        return 1.0, 1.0, 1.0
    tp = len(gold & got)
    p = tp / len(got) if got else 0.0
    r = tp / len(gold) if gold else 0.0
    f1 = (2 * p * r / (p + r)) if (p + r) else 0.0
    return p, r, f1


def extraction_report(graph_dir: Path, gold_dir: Path) -> dict:
    compiled = load_compiled(graph_dir)
    gold = load_gold(gold_dir)

    c_nodes = [f for f in compiled if isinstance(f, Node)]
    c_edges = [f for f in compiled if isinstance(f, Edge)]
    g_nodes = [f for f in gold if isinstance(f, Node)]
    g_edges = [f for f in gold if isinstance(f, Edge)]

    c_ids = {n.id: n for n in c_nodes}
    g_ids = {n.id: n for n in g_nodes}

    c_node_keys = {node_key(n) for n in c_nodes}
    g_node_keys = {node_key(n) for n in g_nodes}
    c_edge_keys = {edge_key(e, c_ids) for e in c_edges}
    g_edge_keys = {edge_key(e, g_ids) for e in g_edges}

    np, nr, nf = precision_recall_f1(g_node_keys, c_node_keys)
    ep, er, ef = precision_recall_f1(g_edge_keys, c_edge_keys)
    return {
        "nodes": {"precision": np, "recall": nr, "f1": nf},
        "edges": {"precision": ep, "recall": er, "f1": ef},
    }
