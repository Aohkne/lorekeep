from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

from typer.testing import CliRunner

from lorekeep.cli import app
from lorekeep.support import build_report, create_bundle

runner = CliRunner()


def _seed_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "data"
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    home.mkdir()
    (home / "config.yaml").write_text(
        "provider:\n"
        "  model: openrouter/deepseek/deepseek-chat\n"
        "  api_key_env: OPENROUTER_API_KEY\n"
        "  api_key: sk-must-never-appear\n"
        "  api_base: https://user:pass@example.test/?token=hidden\n",
        encoding="utf-8",
    )
    graph = home / "graph"
    graph.mkdir()
    (graph / "manifest.json").write_text(json.dumps({
        "schema_version": 3, "chunk_count": 2, "node_count": 3,
        "edge_count": 1, "run_id": "compile-123", "facts_hash": "a" * 64,
        "errors": [{"path": "raw/private.md", "line": 1,
                    "message": "prompt body secret-content"}],
    }), encoding="utf-8")
    logs = home / "logs"
    logs.mkdir()
    (logs / "lorekeep.log").write_text(
        "2026-08-04T00:00:00Z level=ERROR component=lorekeep "
        "event=compile.failed api_key=sk-log-secret\n", encoding="utf-8",
    )
    (home / "raw").mkdir()
    (home / "raw" / "private.md").write_text("raw-private-content", encoding="utf-8")
    return home


def test_report_is_metadata_only_and_handles_missing_service(tmp_path: Path, monkeypatch):
    _seed_home(tmp_path, monkeypatch)
    monkeypatch.setattr("lorekeep.daemon_service.status", lambda: "inactive")
    report = build_report()
    assert "openrouter/deepseek/deepseek-chat" in report
    assert "OPENROUTER_API_KEY" in report
    assert "error_count | 1" in report
    assert "inactive" in report
    assert "event=compile.failed" in report
    for forbidden in ("must-never", "user:pass", "hidden", "private.md",
                      "prompt body", "raw-private-content"):
        assert forbidden not in report


def test_bundle_has_strict_allowlist_and_redacted_tail(tmp_path: Path, monkeypatch):
    _seed_home(tmp_path, monkeypatch)
    monkeypatch.setattr("lorekeep.daemon_service.status", lambda: "inactive")
    output = tmp_path / "support.zip"
    path, digest = create_bundle(output)
    assert path == output
    assert len(digest) == 64
    if os.name != "nt":
        assert output.stat().st_mode & 0o777 == 0o600
    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == {
            "report.md", "logs/runtime-tail.log", "manifest-summary.json",
        }
        combined = b"".join(archive.read(name) for name in archive.namelist()).decode()
    assert "sk-log-secret" not in combined
    assert "raw-private-content" not in combined
    assert "prompt body" not in combined
    assert "[REDACTED]" in combined


def test_support_cli_prints_report_and_creates_bundle(tmp_path: Path, monkeypatch):
    _seed_home(tmp_path, monkeypatch)
    monkeypatch.setattr("lorekeep.daemon_service.status", lambda: "inactive")
    output = tmp_path / "cli-support.zip"
    result = runner.invoke(app, ["support", "--output", str(output)])
    assert result.exit_code == 0, result.output
    assert "# Lorekeep support report" in result.output
    assert output.exists()
    assert "support bundle:" in result.output
    assert "sha256:" in result.output


def test_support_cli_report_only_creates_no_bundle(tmp_path: Path, monkeypatch):
    _seed_home(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("lorekeep.daemon_service.status", lambda: "inactive")
    result = runner.invoke(app, ["support", "--report-only"])
    assert result.exit_code == 0, result.output
    assert "# Lorekeep support report" in result.output
    assert not list(tmp_path.glob("lorekeep-support-*.zip"))


def test_support_cli_no_print_only_outputs_bundle_location(tmp_path: Path, monkeypatch):
    _seed_home(tmp_path, monkeypatch)
    monkeypatch.setattr("lorekeep.daemon_service.status", lambda: "inactive")
    output = tmp_path / "quiet-support.zip"
    result = runner.invoke(app, ["support", "--no-print", "--output", str(output)])
    assert result.exit_code == 0, result.output
    assert "# Lorekeep support report" not in result.output
    assert "support bundle:" in result.output
    assert output.exists()


def test_hidden_legacy_support_aliases_still_work(tmp_path: Path, monkeypatch):
    _seed_home(tmp_path, monkeypatch)
    monkeypatch.setattr("lorekeep.daemon_service.status", lambda: "inactive")
    report = runner.invoke(app, ["support", "report"])
    assert report.exit_code == 0, report.output
    assert "# Lorekeep support report" in report.output
    output = tmp_path / "legacy.zip"
    bundle = runner.invoke(app, ["support", "bundle", "--output", str(output)])
    assert bundle.exit_code == 0, bundle.output
    assert output.exists()


def test_report_survives_invalid_config_and_manifest(tmp_path: Path, monkeypatch):
    home = tmp_path / "data"
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    (home / "graph").mkdir(parents=True)
    (home / "config.yaml").write_text("provider: [", encoding="utf-8")
    (home / "graph" / "manifest.json").write_text("not-json", encoding="utf-8")
    monkeypatch.setattr("lorekeep.daemon_service.status", lambda: "inactive")
    report = build_report()
    assert "invalid (ParserError)" in report
    assert "invalid (JSONDecodeError)" in report
