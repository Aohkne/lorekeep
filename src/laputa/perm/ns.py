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
