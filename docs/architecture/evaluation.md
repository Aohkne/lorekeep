# Testing and evaluation

Lorekeep separates regression tests from benchmark-style evaluation. Regression
tests protect the runtime contract on every change; evaluation commands measure
construction and retrieval quality against datasets.

## Regression suite

The normal suite is offline and deterministic:

```bash
uv run pytest
uv run pytest tests/test_core_regression.py -q
uv run pytest tests/test_determinism.py -q
```

Compile/import/CLI tests inject `FakeProvider`, so they need neither an API key
nor network access. The suite covers component behavior, golden fixtures,
permission closure, temporal filtering, process-safe journals, compile/resolve,
MCP, wiki generation, configuration, integrations, support output, and CLI
contracts.

`tests/test_core_regression.py` is the cross-layer safety net. It checks the
provider class, extraction, compile-to-graph path, graph queries, namespace
filtering, seven-tool MCP surface, wiki projection, and journal resolve. The
determinism tests require unchanged inputs to produce byte-identical graph
output.

Documentation adds its own contract test:

```bash
uv run pytest tests/test_docs_contract.py -q
uv run python scripts/generate_cli_reference.py --check
```

That test keeps the generated command reference aligned with the Typer app,
checks active local Markdown links/anchors, and pins the MCP/config examples to
the current runtime surface.

## Developer evaluation commands

The evaluation commands are intentionally hidden from normal CLI help: they are
maintainer tools, not part of the stable user-facing command surface.

### Construction quality

```bash
LOREKEEP_GOLD=tests/fixtures/gold lorekeep eval
```

`eval` compares the resolved graph with a gold corpus. It reports node and edge
precision/recall/F1 plus graph shape metrics (counts, average degree, density,
and dangling-edge rate). The JSON output defaults to
`.lorekeep/eval/results.json`; override it with `LOREKEEP_EVAL_RESULTS`.

`lorekeep doctor` is different: it is an installation/health check, not a
quality benchmark.

### LoCoMo retrieval

```bash
LOREKEEP_HOME=/tmp/lorekeep-locomo \
  lorekeep eval-locomo --data /path/to/locomo10.json --compile
```

With `--compile`, the command converts the supplied LoCoMo JSON into Markdown,
uses the configured real provider to compile it, then evaluates retrieval. With
an already-built graph, omit `--compile`; the JSON is still required for the QA
items.

The current runner is programmatic, not a coding-agent/answer-generation loop:

1. extract keywords from each question;
2. search the scoped graph;
3. expand one or two graph hops;
4. include referenced source Markdown and a bounded raw-text fallback; and
5. measure answer-token recall (the historical report field and CLI label are
   named `f1`).

It groups results into single-hop, temporal, multi-hop, descriptive, and
adversarial categories. For adversarial items, the current score is one minus
gold-answer token recall (so retrieving unrelated text is not itself penalized).
This makes the harness useful for retrieval regressions, but it must not be
presented as an end-to-end agent reasoning benchmark or a strict abstention
measure.

## Evaluation model

The intended quality ladder is:

| Level | Question | Current status |
|---|---|---|
| Construction | Did compile capture a coherent graph? | Implemented gold-corpus harness |
| Retrieval | Can the scoped query path recover needed evidence? | Implemented LoCoMo-oriented harness |
| Agent reasoning | Does Lorekeep improve complete, temporal, source-backed answers? | Planned |

Future reasoning evaluation should compare the same coding tasks with Lorekeep,
raw-document retrieval, and no knowledge base. Useful rubric dimensions are
correctness, completeness, temporal accuracy, provenance use, abstention, and
reasoning coherence. A curated team-document corpus, calibrated judges, and
human-reviewed labels are still needed before such results are trustworthy.

## Interpretation limits

- A green regression suite proves contract stability, not extraction quality on
  an arbitrary private corpus.
- Construction results depend on the chosen provider/model and gold labels.
- The LoCoMo runner may recover text from raw Markdown after graph-guided search;
  it is not a graph-only score.
- Namespace scope affects every retrieval result. Set `LOREKEEP_NS` explicitly
  when comparing runs.
- Benchmark datasets and provider calls are not bundled into the offline test
  suite.

## Related

- [Compile pipeline](pipeline.md)
- [Permission model](permission.md)
- [Temporal model](temporal.md)
- [Development commands](../../README.md#development)
