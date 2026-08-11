import json
from pathlib import Path
from lorekeep.pipeline import compile_graph, measure_content_quality
from lorekeep.compile.providers import FakeProvider
from lorekeep.models import Schema


def copy_fixture(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(src.read_text())


def test_compile_pipeline_produces_facts(tmp_path: Path, fixtures: Path, caplog):
    import logging as _logging
    raw = tmp_path / "raw"
    copy_fixture(fixtures / "raw/backend/payments.md",
                 raw / "teams/backend/payments.md")
    out = tmp_path / "graph"
    cache = tmp_path / "cache.json"
    schema = Schema.load(json.loads((fixtures / "schema.json").read_text()))

    canned = json.dumps({
        "nodes": [
            {"id": "svc:payments-api", "type": "service", "name": "payments-api",
             "summary": "Main API for payment requests.",
             "props": {"lang": "go"}, "valid_from": "2024-01-15"},
            {"id": "svc:auth", "type": "service", "name": "auth",
             "summary": "Validates service credentials."},
            {"id": "team:backend", "type": "team", "name": "team-backend",
             "summary": "Backend engineering team."},
            {"id": "dec:adr-007", "type": "decision", "name": "adr-007",
             "summary": "Adopts internal request signing.",
             "props": {"title": "payments-api adopts internal signing"}},
        ],
        "edges": [
            {"type": "depends_on", "from": "svc:payments-api", "to": "svc:auth",
             "description": "Uses auth to validate incoming credentials.",
             "valid_from": "2024-01-15", "valid_to": "2025-03-01"},
            {"type": "decided_by", "from": "dec:adr-007", "to": "team:backend",
             "description": "The backend team approved the signing decision."},
        ],
        "aliases": {},
    })
    provider = FakeProvider([canned])

    with caplog.at_level(_logging.INFO, logger="lorekeep"):
        manifest = compile_graph(raw_root=raw, out_dir=out, schema=schema,
                                 provider=provider, cache_path=cache, chunk_lines=60)
    facts = (out / "facts.jsonl").read_text().splitlines()
    assert len(facts) == 6                       # 4 nodes + 2 edges
    assert (out / "manifest.json").exists()
    assert manifest.node_count == 4
    assert manifest.edge_count == 2
    assert any(getattr(r, "event", "") == "compile.start" for r in caplog.records)
    assert any(getattr(r, "event", "") == "compile.complete" for r in caplog.records)
    assert manifest.content_quality is not None
    assert manifest.content_quality.node_summary_coverage == 1.0
    assert manifest.content_quality.edge_description_coverage == 1.0


def test_compile_pipeline_uses_configured_language(
    tmp_path: Path, fixtures: Path, fake_extraction: str,
):
    raw = tmp_path / "raw"
    copy_fixture(
        fixtures / "raw/backend/payments.md",
        raw / "teams/backend/payments.md",
    )
    schema = Schema.load(json.loads((fixtures / "schema.json").read_text()))
    provider = FakeProvider([fake_extraction])

    compile_graph(
        raw_root=raw,
        out_dir=tmp_path / "graph",
        schema=schema,
        provider=provider,
        cache_path=tmp_path / "cache.json",
        language="vi",
    )

    assert "ISO 639-1 code 'vi', regardless" in provider.calls[0][1]


def test_content_quality_reports_generic_edges_and_duplicate_labels():
    from lorekeep.models import Edge, Node

    schema = Schema.load({
        "version": 1,
        "node_types": {"service": {"props": {"name": "string"}}},
        "edge_types": {"relates_to": {"from": "service", "to": "service"}},
    })
    nodes = [
        Node(id="svc:a", type="service", ns=("team",), props={"name": "API", "summary": "First."}),
        Node(id="svc:b", type="service", ns=("team",), props={"name": "API"}),
    ]
    edges = [
        Edge(id="e1", type="relates_to", from_="svc:a", to="svc:b", ns=("team",)),
    ]

    quality = measure_content_quality(nodes, edges, schema)

    assert quality.node_label_coverage == 1.0
    assert quality.node_summary_coverage == 0.5
    assert quality.edge_description_coverage == 0.0
    assert quality.generic_edge_ratio == 1.0
    assert quality.duplicate_label_count == 1


def test_pipeline_per_chunk_failure_logs_exception(tmp_path: Path, fixtures: Path, caplog):
    """A per-chunk failure must be logged (full traceback) for daemon/verbose
    debugging, while the manifest still records the short message."""
    import logging as _logging
    raw = tmp_path / "raw"
    copy_fixture(fixtures / "raw/backend/payments.md",
                 raw / "teams/backend/payments.md")
    schema = Schema.load(json.loads((fixtures / "schema.json").read_text()))

    class _Boom(FakeProvider):
        def extract_json(self, system, user):
            raise RuntimeError("LLM Provider NOT provided")

    with caplog.at_level(_logging.ERROR, logger="lorekeep"):
        manifest = compile_graph(raw_root=raw, out_dir=tmp_path / "graph",
                                 schema=schema, provider=_Boom(responses=[]),
                                 cache_path=tmp_path / "cache.json", chunk_lines=60)
    assert manifest.node_count == 0
    assert manifest.errors                      # short message preserved
    assert any("compile: chunk failed" in r.message for r in caplog.records)
    assert any(r.exc_info for r in caplog.records)  # traceback attached


def test_compile_prev_aliases_forces_merge(tmp_path: Path, fixtures: Path):
    """prev_aliases parameter carries forward merge decisions across recompiles."""
    raw = tmp_path / "raw"
    copy_fixture(fixtures / "raw/backend/payments.md",
                 raw / "teams/backend/payments.md")
    schema = Schema.load(json.loads((fixtures / "schema.json").read_text()))

    canned = json.dumps({
        "nodes": [
            {"id": "svc:payments-api", "type": "service", "name": "payments-api"},
            {"id": "svc:pay", "type": "service", "name": "pay"},
        ],
        "edges": [],
        "aliases": {},
    })
    provider = FakeProvider([canned])

    manifest = compile_graph(
        raw_root=raw, out_dir=tmp_path / "graph", schema=schema,
        provider=provider, cache_path=tmp_path / "cache.json",
        prev_aliases={"svc:pay": "svc:payments-api"},
    )
    # svc:pay must be merged into svc:payments-api
    assert manifest.node_count == 1


def test_compile_without_prev_aliases_no_merge(tmp_path: Path, fixtures: Path):
    """Without prev_aliases, two distinct service nodes remain separate."""
    raw = tmp_path / "raw"
    copy_fixture(fixtures / "raw/backend/payments.md",
                 raw / "teams/backend/payments.md")
    schema = Schema.load(json.loads((fixtures / "schema.json").read_text()))

    canned = json.dumps({
        "nodes": [
            {"id": "svc:payments-api", "type": "service", "name": "payments-api"},
            {"id": "svc:pay", "type": "service", "name": "pay"},
        ],
        "edges": [],
        "aliases": {},
    })
    provider = FakeProvider([canned])

    manifest = compile_graph(
        raw_root=raw, out_dir=tmp_path / "graph", schema=schema,
        provider=provider, cache_path=tmp_path / "cache.json",
    )
    assert manifest.node_count == 2


# ── streaming compile (parallel extraction + flush) ───────────────────────

def _make_multi_chunk_raw(raw: Path, fixtures: Path, copies: int = 3):
    """Create raw/ with multiple chunk-producing files for parallel testing."""
    src = fixtures / "raw/backend/payments.md"
    for i in range(copies):
        dst = raw / f"teams/backend/payments_{i}.md"
        copy_fixture(src, dst)


def test_parallel_extraction_produces_same_result_as_sequential(
    tmp_path: Path, fixtures: Path,
):
    """max_workers=4 must produce identical output to max_workers=1."""
    schema = Schema.load(json.loads((fixtures / "schema.json").read_text()))
    canned = json.dumps({
        "nodes": [
            {"id": "svc:api", "type": "service", "name": "api"},
            {"id": "svc:db", "type": "service", "name": "db"},
        ],
        "edges": [{"type": "depends_on", "from": "svc:api", "to": "svc:db"}],
        "aliases": {},
    })

    # Parallel run
    raw1 = tmp_path / "raw1"
    _make_multi_chunk_raw(raw1, fixtures, copies=3)
    m_parallel = compile_graph(
        raw_root=raw1, out_dir=tmp_path / "g1", schema=schema,
        provider=FakeProvider([canned] * 3),
        cache_path=tmp_path / "c1.json",
        max_workers=4, flush_interval=0,
    )

    # Sequential run
    raw2 = tmp_path / "raw2"
    _make_multi_chunk_raw(raw2, fixtures, copies=3)
    m_seq = compile_graph(
        raw_root=raw2, out_dir=tmp_path / "g2", schema=schema,
        provider=FakeProvider([canned] * 3),
        cache_path=tmp_path / "c2.json",
        max_workers=1, flush_interval=0,
    )

    # Same node/edge counts
    assert m_parallel.node_count == m_seq.node_count
    assert m_parallel.edge_count == m_seq.edge_count
    # Byte-identical facts.jsonl
    assert (tmp_path / "g1/facts.jsonl").read_bytes() == \
           (tmp_path / "g2/facts.jsonl").read_bytes()


def test_streaming_flush_writes_intermediate_graph(
    tmp_path: Path, fixtures: Path,
):
    """flush_interval=2 must write facts.jsonl before all chunks complete."""
    schema = Schema.load(json.loads((fixtures / "schema.json").read_text()))

    def _canned(i):
        return json.dumps({
            "nodes": [{"id": f"svc:svc-{i}", "type": "service", "name": f"svc-{i}"}],
            "edges": [],
            "aliases": {},
        })

    raw = tmp_path / "raw"
    _make_multi_chunk_raw(raw, fixtures, copies=5)

    compile_graph(
        raw_root=raw, out_dir=tmp_path / "graph", schema=schema,
        provider=FakeProvider([_canned(i) for i in range(5)]),
        cache_path=tmp_path / "cache.json",
        max_workers=1,  # sequential to ensure deterministic flush timing
        flush_interval=2,
    )

    # Final output must have all 5 nodes (after dedup)
    facts = (tmp_path / "graph/facts.jsonl").read_text()
    import json as _json
    nodes = [_json.loads(l) for l in facts.splitlines() if l and _json.loads(l)["kind"] == "node"]
    assert len(nodes) == 5


def test_streaming_flush_applies_prev_aliases(
    tmp_path: Path, fixtures: Path,
):
    """Intermediate flushes must apply prev_aliases for graph dedup."""
    schema = Schema.load(json.loads((fixtures / "schema.json").read_text()))

    # Two chunks produce aliases that should merge via prev_aliases
    canned_a = json.dumps({
        "nodes": [{"id": "svc:payments-api", "type": "service", "name": "payments-api"}],
        "edges": [], "aliases": {},
    })
    canned_b = json.dumps({
        "nodes": [{"id": "svc:pay", "type": "service", "name": "pay"}],
        "edges": [], "aliases": {},
    })

    raw = tmp_path / "raw"
    _make_multi_chunk_raw(raw, fixtures, copies=2)

    manifest = compile_graph(
        raw_root=raw, out_dir=tmp_path / "graph", schema=schema,
        provider=FakeProvider([canned_a, canned_b]),
        cache_path=tmp_path / "cache.json",
        max_workers=1, flush_interval=1,
        prev_aliases={"svc:pay": "svc:payments-api"},
    )
    # Must merge into 1 node despite 2 chunks
    assert manifest.node_count == 1


def test_max_workers_1_is_sequential(tmp_path: Path, fixtures: Path):
    """max_workers=1 must behave exactly like the old sequential loop."""
    schema = Schema.load(json.loads((fixtures / "schema.json").read_text()))
    canned = json.dumps({
        "nodes": [{"id": "svc:x", "type": "service", "name": "x"}],
        "edges": [], "aliases": {},
    })
    raw = tmp_path / "raw"
    _make_multi_chunk_raw(raw, fixtures, copies=1)

    manifest = compile_graph(
        raw_root=raw, out_dir=tmp_path / "graph", schema=schema,
        provider=FakeProvider([canned]),
        cache_path=tmp_path / "cache.json",
        max_workers=1, flush_interval=0,
    )
    assert manifest.node_count == 1


def test_parallel_determinism_across_runs(tmp_path: Path, fixtures: Path):
    """Parallel compile must produce byte-identical output across runs."""
    schema = Schema.load(json.loads((fixtures / "schema.json").read_text()))
    canned = json.dumps({
        "nodes": [
            {"id": "svc:api", "type": "service", "name": "api"},
            {"id": "svc:db", "type": "service", "name": "db"},
            {"id": "svc:cache", "type": "service", "name": "cache"},
        ],
        "edges": [
            {"type": "depends_on", "from": "svc:api", "to": "svc:db"},
            {"type": "depends_on", "from": "svc:api", "to": "svc:cache"},
        ],
        "aliases": {},
    })

    results = []
    for i in range(3):
        raw = tmp_path / f"raw{i}"
        _make_multi_chunk_raw(raw, fixtures, copies=3)
        compile_graph(
            raw_root=raw, out_dir=tmp_path / f"g{i}", schema=schema,
            provider=FakeProvider([canned] * 3),
            cache_path=tmp_path / f"c{i}.json",
            max_workers=4, flush_interval=2,
        )
        results.append((tmp_path / f"g{i}/facts.jsonl").read_bytes())

    assert results[0] == results[1] == results[2]


def test_fatal_error_aborts_parallel_compile(tmp_path: Path, fixtures: Path, caplog):
    """A fatal provider error must abort parallel extraction."""
    import logging as _logging
    schema = Schema.load(json.loads((fixtures / "schema.json").read_text()))
    raw = tmp_path / "raw"
    _make_multi_chunk_raw(raw, fixtures, copies=3)

    class _Boom(FakeProvider):
        def extract_json(self, system, user):
            from litellm import AuthenticationError as _AE
            raise _AE("bad key", "openai", "test")

    with caplog.at_level(_logging.ERROR, logger="lorekeep"):
        manifest = compile_graph(
            raw_root=raw, out_dir=tmp_path / "graph", schema=schema,
            provider=_Boom(responses=[]),
            cache_path=tmp_path / "cache.json",
            max_workers=2, flush_interval=0,
        )
    assert manifest.node_count == 0
    assert manifest.errors
    assert any(getattr(r, "event", "") == "compile.aborted_fatal" for r in caplog.records)
