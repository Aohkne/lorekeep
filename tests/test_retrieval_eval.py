from pathlib import Path
from laputa.eval.retrieval import retrieval_report


def test_retrieval_report_all_pass(fixtures: Path, tmp_path: Path):
    import shutil
    out = tmp_path / "graph"
    out.mkdir()
    shutil.copy(fixtures / "gold/payments.facts.jsonl", out / "facts.jsonl")
    rep = retrieval_report(
        graph_dir=out,
        questions_path=fixtures / "retrieval/questions.json",
        allowed_ns=["teams/backend"],
    )
    assert rep["total"] == 3
    assert rep["passed"] == 3
    assert rep["failures"] == []
