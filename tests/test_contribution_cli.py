"""`lorekeep agent contribution` — team-knowledge gap suggestions (read-only)."""
from pathlib import Path
from typer.testing import CliRunner

from lorekeep.cli import app

runner = CliRunner()


def _node_line(id, type, ns):
    import json
    return json.dumps({
        "kind": "node", "id": id, "type": type, "ns": ns,
        "valid_from": None, "valid_to": None, "props": {"name": id}, "src": [],
    })


def test_contribution_finds_personal_only_gap(tmp_path: Path, monkeypatch):
    out = tmp_path / "graph"
    out.mkdir()
    (out / "facts.jsonl").write_text(
        _node_line("svc:shared", "service", ["me", "backend"]) + "\n"
        + _node_line("svc:gap", "service", ["me"]) + "\n"
    )
    cfg = tmp_path / "config.yaml"
    cfg.write_text("ns:\n  default: [me]\n")
    monkeypatch.setenv("LOREKEEP_OUT", str(out))
    monkeypatch.setenv("LOREKEEP_CONFIG", str(cfg))

    result = runner.invoke(app, ["agent", "contribution"])
    assert result.exit_code == 0, result.output
    assert "svc:gap" in result.output           # in me only -> gap
    assert "svc:shared" not in result.output    # also in backend -> not a gap


def test_contribution_no_gap_when_shared(tmp_path: Path, monkeypatch):
    out = tmp_path / "graph"
    out.mkdir()
    (out / "facts.jsonl").write_text(
        _node_line("svc:x", "service", ["me", "backend"]) + "\n"
    )
    cfg = tmp_path / "config.yaml"
    cfg.write_text("ns:\n  default: [me]\n")
    monkeypatch.setenv("LOREKEEP_OUT", str(out))
    monkeypatch.setenv("LOREKEEP_CONFIG", str(cfg))

    result = runner.invoke(app, ["agent", "contribution"])
    assert result.exit_code == 0, result.output
    assert "no contribution gaps" in result.output.lower()


def test_contribution_no_graph(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LOREKEEP_OUT", str(tmp_path / "nope"))
    result = runner.invoke(app, ["agent", "contribution"])
    assert result.exit_code == 1
    assert "compile" in result.output.lower()
