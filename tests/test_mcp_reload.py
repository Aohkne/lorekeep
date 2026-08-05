"""Lazy-reload: the server refreshes the graph when facts.jsonl changes on disk,
so memory updates (lorekeep compile) are visible without reconnecting the MCP client.
"""
import shutil
from pathlib import Path

import lorekeep.mcp_server as ms


def test_lazy_reload_on_facts_change(tmp_path: Path, fixtures: Path):
    d = tmp_path / "graph"
    d.mkdir()
    shutil.copy(fixtures / "gold/payments.facts.jsonl", d / "facts.jsonl")
    ms.configure(graph_dir=d, allowed_ns=["backend"], schema_path=fixtures / "schema.json")
    assert ms.get_node("svc:auth")["id"] == "svc:auth"

    # rewrite facts.jsonl removing svc:auth (new mtime)
    lines = [l for l in (d / "facts.jsonl").read_text().splitlines() if "svc:auth" not in l]
    (d / "facts.jsonl").write_text("\n".join(lines) + "\n")

    # next query auto-reloads -> svc:auth gone
    assert "error" in ms.get_node("svc:auth")


def test_no_reload_when_unchanged(tmp_path: Path, fixtures: Path):
    d = tmp_path / "graph"
    d.mkdir()
    shutil.copy(fixtures / "gold/payments.facts.jsonl", d / "facts.jsonl")
    ms.configure(graph_dir=d, allowed_ns=["backend"], schema_path=fixtures / "schema.json")
    m1 = ms._state.get("facts_mtime")
    ms.get_node("svc:auth")                       # query; no file change
    assert ms._state.get("facts_mtime") == m1     # no reload fired


def test_fts_failure_is_logged_without_stopping_mcp(tmp_path, fixtures, monkeypatch, caplog):
    d = tmp_path / "graph"
    d.mkdir()
    shutil.copy(fixtures / "gold/payments.facts.jsonl", d / "facts.jsonl")

    class BrokenFTS:
        def __init__(self, _path):
            raise OSError("private index detail")

    monkeypatch.setattr(ms, "FTSIndex", BrokenFTS)
    with caplog.at_level("WARNING", logger="lorekeep.mcp"):
        ms.configure(graph_dir=d, allowed_ns=["backend"], schema_path=fixtures / "schema.json")
    assert ms._fts is None
    assert "FTS unavailable" in caplog.text
    assert "private index detail" not in caplog.text
