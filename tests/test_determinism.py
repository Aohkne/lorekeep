import json
from pathlib import Path
from laputa.pipeline import compile_graph
from laputa.compile.providers import FakeProvider
from laputa.models import Schema


def test_recompile_is_byte_identical(tmp_path: Path, fixtures: Path):
    def make_canned():
        return json.dumps({
            "nodes": [
                {"id": "svc:payments-api", "type": "service", "name": "payments-api",
                 "props": {"lang": "go"}, "valid_from": "2024-01-15"},
                {"id": "svc:auth", "type": "service", "name": "auth"},
            ],
            "edges": [{"type": "depends_on", "from": "svc:payments-api",
                       "to": "svc:auth", "valid_from": "2024-01-15",
                       "valid_to": "2025-03-01"}],
            "aliases": {},
        })

    raw = tmp_path / "raw"
    (raw / "teams/backend").mkdir(parents=True)
    (raw / "teams/backend/p.md").write_text(
        (fixtures / "raw/backend/payments.md").read_text())
    schema = Schema.load(json.loads((fixtures / "schema.json").read_text()))

    out1, out2 = tmp_path / "g1", tmp_path / "g2"
    compile_graph(raw, out1, schema, FakeProvider([make_canned()]),
                  tmp_path / "c1.json")
    compile_graph(raw, out2, schema, FakeProvider([make_canned()]),
                  tmp_path / "c2.json")

    b1 = (out1 / "facts.jsonl").read_bytes()
    b2 = (out2 / "facts.jsonl").read_bytes()
    assert b1 == b2, "re-compile of identical input must be byte-identical"


def test_cache_makes_recompile_identical_without_new_llm_call(tmp_path: Path, fixtures: Path):
    raw = tmp_path / "raw"
    (raw / "teams/backend").mkdir(parents=True)
    (raw / "teams/backend/p.md").write_text(
        (fixtures / "raw/backend/payments.md").read_text())
    schema = Schema.load(json.loads((fixtures / "schema.json").read_text()))
    cache = tmp_path / "shared.json"

    canned = json.dumps({"nodes": [{"id": "svc:x", "type": "service", "name": "x"}],
                         "edges": [], "aliases": {}})
    # first run consumes the one canned response; second run must use cache
    compile_graph(raw, tmp_path / "g1", schema, FakeProvider([canned]), cache)
    compile_graph(raw, tmp_path / "g2", schema, FakeProvider([]), cache)   # no responses!
    assert (tmp_path / "g1/facts.jsonl").read_bytes() == \
           (tmp_path / "g2/facts.jsonl").read_bytes()
