from laputa.models import Node, Edge
from laputa.perm.ns import effective_ns, is_node_visible, is_edge_visible


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
