from lorekeep.models import Node, Edge
from lorekeep.perm.ns import effective_ns, expand_namespaces, is_node_visible, is_edge_visible


def n(id, ns):
    return Node(id=id, type="service", ns=tuple(ns), props={"name": id})


def e(frm, to, ns):
    return Edge(id="e1", type="depends_on", **{"from": frm}, to=to, ns=tuple(ns))


def test_effective_ns_includes_public():
    assert effective_ns(["teams/backend"]) == {"teams/backend", "public"}


def test_node_visible_via_public():
    eff = effective_ns(["teams/frontend"])
    assert is_node_visible(n("x", ["public"]), eff)
    assert not is_node_visible(n("y", ["teams/backend"]), eff)


def test_edge_requires_both_endpoints_and_edge_ns():
    eff = effective_ns(["teams/backend"])
    a = n("a", ["teams/backend"])
    b = n("b", ["teams/backend"])
    c = n("c", ["teams/frontend"])          # hidden from backend
    assert is_edge_visible(e("a", "b", ["teams/backend"]), a, b, eff)
    # endpoint c hidden -> edge invisible even if edge.ns is backend
    assert not is_edge_visible(e("a", "c", ["teams/backend"]), a, c, eff)
    # edge crosses into a ns the caller lacks -> invisible
    assert not is_edge_visible(e("a", "b", ["teams/frontend"]), a, b, eff)


def test_deny_default_when_endpoint_none():
    eff = effective_ns(["teams/backend"])
    a = n("a", ["teams/backend"])
    assert not is_edge_visible(e("a", "ghost", ["teams/backend"]), a, None, eff)


from lorekeep.store.graph import GraphStore


def store_with_cross_ns(tmp_path):
    # backend node a, frontend node c, edge a->c tagged backend (cross-ns)
    import json
    facts = [
        {"kind": "node", "id": "a", "type": "service", "ns": ["teams/backend"], "props": {"name": "a"}},
        {"kind": "node", "id": "c", "type": "service", "ns": ["teams/frontend"], "props": {"name": "c"}},
        {"kind": "edge", "id": "e1", "type": "depends_on", "from": "a", "to": "c", "ns": ["teams/backend"]},
    ]
    p = tmp_path / "facts.jsonl"
    p.write_text("\n".join(json.dumps(f) for f in facts))
    return GraphStore.from_jsonl(p)


def test_scoped_get_node_hides_other_ns(tmp_path):
    g = store_with_cross_ns(tmp_path)
    from lorekeep.perm.ns import ScopedGraph
    scoped = ScopedGraph(g, ["teams/backend"])
    assert scoped.get_node("a") is not None
    assert scoped.get_node("c") is None              # frontend hidden


def test_scoped_neighbors_hides_cross_ns_edge(tmp_path):
    g = store_with_cross_ns(tmp_path)
    from lorekeep.perm.ns import ScopedGraph
    scoped = ScopedGraph(g, ["teams/backend"])
    nb = scoped.neighbors("a", depth=1)
    ids = {n.id for n in nb["nodes"]}
    assert ids == {"a"}                              # c hidden, edge a->c dropped
    assert nb["edges"] == []


def test_scoped_public_caller_sees_public_only(tmp_path):
    g = store_with_cross_ns(tmp_path)
    from lorekeep.perm.ns import ScopedGraph
    scoped = ScopedGraph(g, [])                       # only public
    assert scoped.get_node("a") is None


def test_scoped_snapshot_filters_hidden(tmp_path):
    g = store_with_cross_ns(tmp_path)
    from lorekeep.perm.ns import ScopedGraph
    from lorekeep.store.graph import parse_date
    scoped = ScopedGraph(g, ["teams/frontend"])
    nodes, edges = scoped.snapshot(parse_date("2024-01-15"))
    assert {n.id for n in nodes} == {"c"}            # backend hidden


def test_scoped_history_empty_for_hidden(tmp_path):
    g = store_with_cross_ns(tmp_path)
    from lorekeep.perm.ns import ScopedGraph
    scoped = ScopedGraph(g, ["teams/frontend"])
    assert scoped.history("a") == []                 # a hidden


def test_scoped_changes_filters_edges_to_hidden(tmp_path):
    g = store_with_cross_ns(tmp_path)
    from lorekeep.perm.ns import ScopedGraph
    from lorekeep.store.graph import parse_date
    scoped = ScopedGraph(g, ["teams/backend"])
    rep = scoped.changes(parse_date("2000-01-01"), parse_date("2100-01-01"))
    # edge a->c: a visible, c hidden -> edge dropped
    assert rep["began"] == [] and rep["ended"] == []


def test_list_namespaces(tmp_path):
    g = store_with_cross_ns(tmp_path)
    from lorekeep.perm.ns import ScopedGraph
    scoped = ScopedGraph(g, ["teams/backend"])
    assert scoped.list_namespaces() == ["public", "teams/backend"]


def test_scoped_search_filters_hidden(tmp_path):
    g = store_with_cross_ns(tmp_path)
    from lorekeep.perm.ns import ScopedGraph
    scoped = ScopedGraph(g, ["teams/frontend"])
    # search 'a' would match node a (backend) and c (frontend name 'c' has no 'a'? c text has 'c')
    # node a is backend -> hidden; only visible results returned
    res = scoped.search("a")
    assert "a" not in res


def test_store_all_namespaces(tmp_path):
    g = store_with_cross_ns(tmp_path)
    assert g.all_namespaces() == {"teams/backend", "teams/frontend"}


def test_scoped_store_property_exposes_unscoped_graph(tmp_path):
    g = store_with_cross_ns(tmp_path)
    from lorekeep.perm.ns import ScopedGraph
    scoped = ScopedGraph(g, ["teams/backend"])
    assert scoped.store is g
    assert scoped.store.all_namespaces() == {"teams/backend", "teams/frontend"}


# ── wildcard / pattern expansion ────────────────────────────────────────────

_GRAPH_NS = {"me", "claude-session", "claude-memory", "codex-session",
             "codex-memory", "grok-session", "public"}


def test_expand_star_matches_all_graph_namespaces():
    assert expand_namespaces(["*"], _GRAPH_NS) == _GRAPH_NS


def test_expand_glob_matches_subset():
    result = expand_namespaces(["*-session"], _GRAPH_NS)
    assert result == {"claude-session", "codex-session", "grok-session"}


def test_expand_glob_matches_prefix():
    result = expand_namespaces(["claude-*"], _GRAPH_NS)
    assert result == {"claude-session", "claude-memory"}


def test_expand_literal_kept_even_if_not_in_graph():
    result = expand_namespaces(["me"], set())
    assert result == {"me"}


def test_expand_mixed_literal_and_glob():
    result = expand_namespaces(["me", "*-session"], _GRAPH_NS)
    assert result == {"me", "claude-session", "codex-session", "grok-session"}


def test_expand_literal_in_graph_kept():
    result = expand_namespaces(["public"], _GRAPH_NS)
    assert result == {"public"}


def test_expand_no_wildcard_is_identity_on_known_ns():
    result = expand_namespaces(["me", "claude-session"], _GRAPH_NS)
    assert result == {"me", "claude-session"}


def test_expand_glob_no_match_returns_empty():
    result = expand_namespaces(["*-nonexistent"], _GRAPH_NS)
    assert result == set()
