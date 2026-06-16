from pathlib import Path
from lorekeep.models import Node
from lorekeep.store.fts import FTSIndex, scan_search, node_text


def nd(id, name, lang=None):
    props = {"name": name}
    if lang:
        props["lang"] = lang
    return Node(id=id, type="service", ns=("teams/backend",), props=props)


def test_node_text_concatenates_id_name_props():
    t = node_text(nd("svc:x", "auth", "go"))
    assert "svc:x" in t and "auth" in t and "go" in t


def test_scan_search_substring(tmp_path: Path):
    nodes = [nd("svc:a", "payments-api"), nd("svc:b", "auth")]
    assert scan_search(nodes, "pay") == ["svc:a"]
    assert scan_search(nodes, "auth") == ["svc:b"]
    assert scan_search(nodes, "zzz") == []


def test_fts_index_build_and_match(tmp_path: Path):
    idx = FTSIndex(tmp_path / "fts.sqlite")
    idx.build([nd("svc:a", "payments"), nd("svc:b", "auth")])
    assert "svc:a" in idx.search("payments")
    assert idx.search("payments") == ["svc:a"]
    assert idx.search("nomatch*") == []
    idx.close()
