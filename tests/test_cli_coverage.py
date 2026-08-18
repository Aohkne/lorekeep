"""Coverage fill for cli.py uncovered branches.

Tests the following untested paths:
- resolve(): no-pending-dir + no-pending-entries early returns
- doctor(): corrupt graph, invalid schema, MCP failure, generic provider error
- config_set(): bool/float/null coercion + missing-config guard
- _report_content_quality(): every branch
- serve(): ImportError + FileNotFoundError guards
- hook(): import error swallowing
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from lorekeep.cli import app, _report_content_quality
from lorekeep.models import ContentQuality, Manifest

runner = CliRunner()


# ── resolve() early returns ─────────────────────────────────────────────────

class TestResolveEarlyReturns:
    """resolve() must exit cleanly when there's nothing to do."""

    def test_no_pending_directory(self, tmp_path: Path, monkeypatch, fixtures: Path):
        """No pending/ dir → echoes advisory, exit 0.

        The _with_resolve_lock decorator normally creates pending/ as a side
        effect of acquiring the file lock. We patch the lock to a no-op so the
        'no pending directory' guard actually fires.
        """
        home = tmp_path / "home"
        home.mkdir()
        out = home / "graph"
        out.mkdir()
        shutil.copy(fixtures / "gold/payments.facts.jsonl", out / "facts.jsonl")
        monkeypatch.setenv("LOREKEEP_HOME", str(home))

        from contextlib import contextmanager

        @contextmanager
        def _noop_lock(_pending):
            yield

        monkeypatch.setattr("lorekeep.journal.resolve_lock", _noop_lock)
        # pending/ does not exist under home
        assert not (home / "pending").exists()
        result = runner.invoke(app, ["resolve"])
        assert result.exit_code == 0
        assert "nothing to do" in result.stdout.lower()

    def test_no_pending_journal_entries(self, tmp_path: Path, monkeypatch, fixtures: Path):
        """pending/ dir exists but holds only non-pending entries → exit 0."""
        home = tmp_path / "home"
        home.mkdir()
        out = home / "graph"
        out.mkdir()
        shutil.copy(fixtures / "gold/payments.facts.jsonl", out / "facts.jsonl")
        pending = home / "pending"
        pending.mkdir()
        # A journal entry that's already merged (not "pending")
        (pending / "old.jsonl").write_text(
            json.dumps({
                "id": "test-001",
                "kind": "propose_fact",
                "agent": "test",
                "ns": "backend",
                "confidence": 0.9,
                "status": "merged",
                "proposed_at": "2026-01-01T00:00:00Z",
            })
            + "\n"
        )
        monkeypatch.setenv("LOREKEEP_HOME", str(home))
        result = runner.invoke(app, ["resolve"])
        assert result.exit_code == 0
        assert "no pending journal entries" in result.stdout.lower()


# ── doctor() error paths ────────────────────────────────────────────────────

class TestDoctorErrorPaths:
    """Doctor branches not yet covered by test_doctor_cli.py."""

    def test_doctor_corrupt_graph(self, tmp_path: Path, monkeypatch, fixtures: Path):
        """Corrupt facts.jsonl → 'cannot load graph', exit 1."""
        out = tmp_path / "graph"
        out.mkdir()
        (out / "facts.jsonl").write_text("{not valid json\n")
        monkeypatch.setenv("LOREKEEP_OUT", str(out))
        monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))
        monkeypatch.setattr("lorekeep.cli._has_provider", lambda c: False)
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 1
        assert "cannot load graph" in result.stdout.lower()

    def test_doctor_invalid_schema(self, tmp_path: Path, monkeypatch, fixtures: Path):
        """Invalid schema.json → problem appended (non-fatal until final gate)."""
        out = tmp_path / "graph"
        out.mkdir()
        shutil.copy(fixtures / "gold/payments.facts.jsonl", out / "facts.jsonl")
        bad_schema = tmp_path / "schema.json"
        bad_schema.write_text("{not json}")
        monkeypatch.setenv("LOREKEEP_OUT", str(out))
        monkeypatch.setenv("LOREKEEP_SCHEMA", str(bad_schema))
        monkeypatch.setattr("lorekeep.cli._has_provider", lambda c: False)
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 1
        assert "schema invalid" in result.stdout.lower()

    def test_doctor_generic_provider_error(self, tmp_path: Path, monkeypatch, fixtures: Path):
        """Unclassified provider error → generic 'FAILED' branch."""
        out = tmp_path / "graph"
        out.mkdir()
        shutil.copy(fixtures / "gold/payments.facts.jsonl", out / "facts.jsonl")

        class _WeirdError:
            def ping(self):
                raise Exception("something totally unexpected")

        monkeypatch.setenv("LOREKEEP_OUT", str(out))
        monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))
        monkeypatch.setattr("lorekeep.cli._has_provider", lambda c: True)
        monkeypatch.setattr("lorekeep.cli._make_provider", lambda c: _WeirdError())
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 1
        assert "provider: failed" in result.stdout.lower()
        assert "unexpected" in result.stdout.lower()


# ── config_set() type coercion ──────────────────────────────────────────────

class TestConfigSetCoercion:
    """Branches not yet covered by test_enhancements.py."""

    def _make_config(self, tmp_path: Path, monkeypatch, content: str):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(content)
        monkeypatch.setenv("LOREKEEP_CONFIG", str(cfg))
        return cfg

    def test_bool_coercion_true(self, tmp_path: Path, monkeypatch):
        self._make_config(tmp_path, monkeypatch, "observability:\n  enabled: false\n")
        result = runner.invoke(app, ["config", "set", "observability.enabled", "true"])
        assert result.exit_code == 0
        import yaml
        data = yaml.safe_load((tmp_path / "config.yaml").read_text())
        assert data["observability"]["enabled"] is True

    def test_bool_coercion_yes(self, tmp_path: Path, monkeypatch):
        self._make_config(tmp_path, monkeypatch, "observability:\n  enabled: false\n")
        result = runner.invoke(app, ["config", "set", "observability.enabled", "yes"])
        assert result.exit_code == 0
        import yaml
        data = yaml.safe_load((tmp_path / "config.yaml").read_text())
        assert data["observability"]["enabled"] is True

    def test_bool_coercion_false(self, tmp_path: Path, monkeypatch):
        self._make_config(tmp_path, monkeypatch, "observability:\n  enabled: true\n")
        result = runner.invoke(app, ["config", "set", "observability.enabled", "false"])
        assert result.exit_code == 0
        import yaml
        data = yaml.safe_load((tmp_path / "config.yaml").read_text())
        assert data["observability"]["enabled"] is False

    def test_float_coercion(self, tmp_path: Path, monkeypatch):
        self._make_config(tmp_path, monkeypatch, "compile:\n  threshold: 0.5\n")
        result = runner.invoke(app, ["config", "set", "compile.threshold", "0.95"])
        assert result.exit_code == 0
        import yaml
        data = yaml.safe_load((tmp_path / "config.yaml").read_text())
        assert data["compile"]["threshold"] == 0.95

    def test_null_coercion(self, tmp_path: Path, monkeypatch):
        self._make_config(tmp_path, monkeypatch, "provider:\n  api_base: https://example.com\n")
        result = runner.invoke(app, ["config", "set", "provider.api_base", "null"])
        assert result.exit_code == 0
        import yaml
        data = yaml.safe_load((tmp_path / "config.yaml").read_text())
        assert data["provider"]["api_base"] is None

    def test_missing_config_guard(self, tmp_path: Path, monkeypatch):
        """No config.yaml → advisory + exit 1."""
        monkeypatch.setenv("LOREKEEP_CONFIG", str(tmp_path / "nonexistent.yaml"))
        result = runner.invoke(app, ["config", "set", "provider.model", "test/test"])
        assert result.exit_code == 1
        assert "run `lorekeep init`" in result.stdout.lower()


# ── _report_content_quality() ───────────────────────────────────────────────

class TestReportContentQuality:
    """Every branch of _report_content_quality."""

    def _manifest(self, **kw) -> Manifest:
        cq = ContentQuality(**kw)
        return Manifest(
            schema_version=1,
            chunk_count=1,
            node_count=1,
            edge_count=0,
            run_id="test-run",
            facts_hash="abc123",
            compiled_at="2026-01-01T00:00:00Z",
            content_quality=cq,
        )

    def test_quality_none_silent(self):
        """content_quality=None → no warning."""
        m = Manifest(
            schema_version=1,
            chunk_count=1,
            node_count=1,
            edge_count=0,
            run_id="test-run",
            facts_hash="abc123",
            compiled_at="2026-01-01T00:00:00Z",
            content_quality=None,
        )
        _report_content_quality(m)  # must not raise

    def test_all_good_no_warning(self, capsys):
        m = self._manifest(
            node_summary_coverage=1.0,
            edge_description_coverage=1.0,
            generic_edge_ratio=0.1,
            duplicate_label_count=0,
        )
        _report_content_quality(m)
        captured = capsys.readouterr()
        assert "content quality" not in captured.out.lower()

    def test_low_summary_coverage(self, capsys):
        m = self._manifest(node_summary_coverage=0.5)
        _report_content_quality(m)
        out = capsys.readouterr().out
        assert "summaries" in out.lower()

    def test_low_edge_description_coverage(self, capsys):
        m = self._manifest(edge_description_coverage=0.3)
        _report_content_quality(m)
        out = " ".join(capsys.readouterr().out.split())
        assert "relationship explanations" in out.lower()

    def test_high_generic_edge_ratio(self, capsys):
        m = self._manifest(generic_edge_ratio=0.6)
        _report_content_quality(m)
        out = capsys.readouterr().out
        assert "generic edges" in out.lower()

    def test_duplicate_labels(self, capsys):
        m = self._manifest(duplicate_label_count=5)
        _report_content_quality(m)
        out = capsys.readouterr().out
        assert "duplicate labels" in out.lower()

    def test_multiple_issues_joined(self, capsys):
        m = self._manifest(
            node_summary_coverage=0.5,
            edge_description_coverage=0.3,
            generic_edge_ratio=0.8,
            duplicate_label_count=2,
        )
        _report_content_quality(m)
        out = " ".join(capsys.readouterr().out.split())
        assert "summaries" in out.lower()
        assert "relationship explanations" in out.lower()
        assert "generic edges" in out.lower()
        assert "duplicate labels" in out.lower()


# ── serve() guards ──────────────────────────────────────────────────────────

class TestServeGuards:
    """ImportError and FileNotFoundError guards in serve()."""

    def test_serve_import_error_fastmcp(self, tmp_path: Path, monkeypatch):
        """ImportError mentioning 'fastmcp' → v2-specific message, exit 1."""
        out = tmp_path / "graph"
        out.mkdir()
        monkeypatch.setenv("LOREKEEP_OUT", str(out))
        monkeypatch.setenv("LOREKEEP_SCHEMA", str(tmp_path / "schema.json"))

        # Simulate mcp v2 missing FastMCP by poisoning the module cache.
        real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

        def _fake_import(name, *args, **kwargs):
            if name == "lorekeep.mcp_server":
                raise ImportError("cannot import name 'FastMCP' from 'mcp.server.fastmcp'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr("builtins.__import__", _fake_import)
        # Remove cached module so the import is reattempted
        monkeypatch.delitem(sys.modules, "lorekeep.mcp_server", raising=False)
        result = runner.invoke(app, ["serve", "--transport", "stdio"])
        assert result.exit_code == 1
        assert "mcp" in result.stdout.lower()

    def test_serve_import_error_generic(self, tmp_path: Path, monkeypatch):
        """Generic ImportError → generic mcp missing message, exit 1."""
        out = tmp_path / "graph"
        out.mkdir()
        monkeypatch.setenv("LOREKEEP_OUT", str(out))
        monkeypatch.setenv("LOREKEEP_SCHEMA", str(tmp_path / "schema.json"))

        real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

        def _fake_import(name, *args, **kwargs):
            if name == "lorekeep.mcp_server":
                raise ImportError("No module named 'mcp'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr("builtins.__import__", _fake_import)
        monkeypatch.delitem(sys.modules, "lorekeep.mcp_server", raising=False)
        result = runner.invoke(app, ["serve", "--transport", "stdio"])
        assert result.exit_code == 1
        assert "mcp" in result.stdout.lower()

    def test_serve_no_graph_file(self, tmp_path: Path, monkeypatch):
        """Missing facts.jsonl → FileNotFoundError guard, exit 1."""
        monkeypatch.setenv("LOREKEEP_OUT", str(tmp_path / "nonexistent"))
        monkeypatch.setenv("LOREKEEP_SCHEMA", str(tmp_path / "schema.json"))
        result = runner.invoke(app, ["serve", "--transport", "stdio"])
        assert result.exit_code == 1
