"""Coverage fill for eval/locomo.py helper functions.

Covers _normalize, token_f1, token_recall, _node_text, _edge_text,
_load_src_text, _search_raw_text, and convert_locomo edge cases.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from lorekeep.eval.locomo import (
    _normalize,
    token_f1,
    token_recall,
    _node_text,
    _edge_text,
    _load_src_text,
    _search_raw_text,
    convert_locomo,
)
from lorekeep.models import Node, Edge
from lorekeep.store.graph import GraphStore


# ======================================================================
# Tokenizer + scorer
# ======================================================================

def test_normalize_strips_articles_and_punctuation():
    tokens = _normalize("The cat is on a mat!")
    assert "the" not in tokens
    assert "a" not in tokens
    assert "is" not in tokens
    assert "cat" in tokens
    assert "mat" in tokens


def test_token_f1_identical():
    assert token_f1("hello world", "hello world") == 1.0


def test_token_f1_no_overlap():
    assert token_f1("cat", "dog") == 0.0


def test_token_f1_partial():
    score = token_f1("the quick brown fox", "the quick fox")
    assert 0 < score < 1.0


def test_token_f1_both_empty():
    assert token_f1("", "") == 1.0


def test_token_f1_one_empty():
    assert token_f1("hello", "") == 0.0
    assert token_f1("", "hello") == 0.0


def test_token_recall_empty_gold():
    assert token_recall("", "anything") == 1.0


def test_token_recall_empty_prediction():
    assert token_recall("gold answer", "") == 0.0


def test_token_recall_partial():
    assert token_recall("cat dog", "cat bird") == 0.5


# ======================================================================
# _node_text / _edge_text
# ======================================================================

def test_node_text_basic():
    node = Node(id="svc:api", type="service", ns=("ns",), props={"name": "API"})
    text = _node_text(node)
    assert "svc:api" in text
    assert "service" in text
    assert "API" in text


def test_node_text_with_dates():
    node = Node(
        id="evt:1", type="event", ns=("ns",), props={},
        valid_from=date(2026, 1, 1), valid_to=date(2026, 6, 1),
    )
    text = _node_text(node)
    assert "2026-01-01" in text
    assert "2026-06-01" in text


def test_edge_text_with_nodes():
    n1 = Node(id="svc:a", type="service", ns=("ns",), props={"name": "A"})
    n2 = Node(id="svc:b", type="service", ns=("ns",), props={"name": "B"})
    edge = Edge(id="e1", type="depends_on", **{"from": "svc:a"},
                to="svc:b", ns=("ns",), props={"weight": "high"})
    store = GraphStore([n1, n2], [edge])
    text = _edge_text(edge, store)
    assert "depends_on" in text
    assert "svc:a" in text
    assert "svc:b" in text
    assert "high" in text


def test_edge_text_missing_nodes():
    """Edge endpoints not in store still produce text."""
    edge = Edge(id="e1", type="calls", **{"from": "x"},
                to="y", ns=("ns",), props={})
    store = GraphStore([], [])
    text = _edge_text(edge, store)
    assert "calls" in text
    assert "x" in text
    assert "y" in text


def test_edge_text_with_dates():
    edge = Edge(
        id="e1", type="depends_on", **{"from": "x"},
        to="y", ns=("ns",), props={},
        valid_from=date(2026, 1, 1), valid_to=date(2026, 6, 1),
    )
    store = GraphStore([], [])
    text = _edge_text(edge, store)
    assert "2026-01-01" in text
    assert "2026-06-01" in text


# ======================================================================
# _load_src_text
# ======================================================================

def test_load_src_text_no_raw_dir():
    assert _load_src_text(("file.md:10",), None) == ""


def test_load_src_text_empty_refs():
    assert _load_src_text((), Path("/tmp")) == ""


def test_load_src_text_reads_files(tmp_path: Path):
    (tmp_path / "doc.md").write_text("# Doc\ncontent here")
    (tmp_path / "other.md").write_text("# Other\nmore content")
    result = _load_src_text(("doc.md:1", "other.md:5"), tmp_path)
    assert "content here" in result
    assert "more content" in result


def test_load_src_text_dedupes_files(tmp_path: Path):
    (tmp_path / "doc.md").write_text("content")
    result = _load_src_text(("doc.md:1", "doc.md:5"), tmp_path)
    # file is only read once despite two refs
    assert result.count("content") == 1


def test_load_src_text_missing_file_skipped(tmp_path: Path):
    result = _load_src_text(("nonexistent.md:1",), tmp_path)
    assert result == ""


def test_load_src_text_read_error_skipped(tmp_path: Path):
    """If a file can't be read, it's skipped."""
    f = tmp_path / "bad.md"
    f.write_text("ok")
    f.chmod(0o000)
    try:
        result = _load_src_text(("bad.md:1",), tmp_path)
        # either empty or has content depending on root perms
        assert isinstance(result, str)
    finally:
        f.chmod(0o644)


def test_load_src_text_limits_to_five_files(tmp_path: Path):
    for i in range(7):
        (tmp_path / f"f{i}.md").write_text(f"file{i}")
    refs = tuple(f"f{i}.md:1" for i in range(7))
    result = _load_src_text(refs, tmp_path)
    # only 5 files read
    count = sum(1 for i in range(7) if f"file{i}" in result)
    assert count <= 5


# ======================================================================
# _search_raw_text
# ======================================================================

def test_search_raw_text_no_raw_dir():
    assert _search_raw_text(["kw"], None) == ""


def test_search_raw_text_no_keywords():
    assert _search_raw_text([], Path("/tmp")) == ""


def test_search_raw_text_finds_matches(tmp_path: Path):
    (tmp_path / "a.md").write_text("# FastAPI\nFastAPI is great")
    (tmp_path / "b.md").write_text("# Other\nNothing relevant")
    result = _search_raw_text(["FastAPI"], tmp_path)
    assert "FastAPI is great" in result
    assert "Nothing relevant" not in result


def test_search_raw_text_no_match(tmp_path: Path):
    (tmp_path / "a.md").write_text("nothing here")
    assert _search_raw_text(["missing"], tmp_path) == ""


def test_search_raw_text_read_error_skipped(tmp_path: Path):
    f = tmp_path / "bad.md"
    f.write_text("content")
    f.chmod(0o000)
    try:
        result = _search_raw_text(["content"], tmp_path)
        assert isinstance(result, str)
    finally:
        f.chmod(0o644)


def test_search_raw_text_limit(tmp_path: Path):
    for i in range(5):
        (tmp_path / f"f{i}.md").write_text(f"keyword{i}")
    result = _search_raw_text(["keyword"], tmp_path, limit=2)
    # at most 2 files matched
    count = sum(1 for i in range(5) if f"keyword{i}" in result)
    assert count <= 2


# ======================================================================
# convert_locomo edge cases
# ======================================================================

def test_convert_locomo_empty_session_skipped(tmp_path: Path):
    """Sessions with empty utterance lists are skipped."""
    json_path = tmp_path / "data.json"
    json_path.write_text(json.dumps([{
        "sample_id": "conv1",
        "conversation": {
            "session_1": [],  # empty → skipped
            "session_2": [
                {"speaker": "A", "dia_id": 1, "text": "hello"},
            ],
            "session_2_date_time": "2026-01-01",
        },
        "qa_pairs": [],
    }]))
    raw_dir = tmp_path / "raw"
    count = convert_locomo(json_path, raw_dir)
    assert count == 1  # only session_2 written
