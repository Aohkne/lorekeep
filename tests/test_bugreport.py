"""Tests for the auto GitHub issue reporting handler."""
from __future__ import annotations

import json
import logging
import os
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from lorekeep.bugreport import (
    BugReportHandler,
    _build_issue_body,
    _create_github_issue,
    _load_dedup,
    _save_dedup,
    _signature,
)
from lorekeep.cli import app

runner = CliRunner()


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_record(
    level: int = logging.ERROR,
    msg: str = "compile failed",
    event: str = "compile.chunk_failed",
    exc_info: tuple | None = None,
    name: str = "lorekeep",
) -> logging.LogRecord:
    record = logging.LogRecord(
        name=name,
        level=level,
        pathname="pipeline.py",
        lineno=42,
        msg=msg,
        args=(),
        exc_info=exc_info,
    )
    record.event = event
    return record


def _make_error_record(msg: str = "compile failed") -> logging.LogRecord:
    try:
        raise ValueError("bad chunk")
    except ValueError:
        import sys
        return _make_record(exc_info=sys.exc_info())


# ── signature ────────────────────────────────────────────────────────────────


class TestSignature:
    def test_stable(self):
        sig_a = _signature("compile.failed", "ValueError", "lorekeep")
        sig_b = _signature("compile.failed", "ValueError", "lorekeep")
        assert sig_a == sig_b

    def test_different_event_different_sig(self):
        sig_a = _signature("compile.failed", "ValueError", "lorekeep")
        sig_b = _signature("mcp.failed", "ValueError", "lorekeep")
        assert sig_a != sig_b

    def test_different_error_type_different_sig(self):
        sig_a = _signature("compile.failed", "ValueError", "lorekeep")
        sig_b = _signature("compile.failed", "RuntimeError", "lorekeep")
        assert sig_a != sig_b

    def test_is_short_hex(self):
        sig = _signature("a", "b", "c")
        assert len(sig) == 16
        int(sig, 16)  # must be valid hex


# ── dedup persistence ────────────────────────────────────────────────────────


class TestDedup:
    def test_load_missing_file_returns_empty(self, tmp_path: Path):
        assert _load_dedup(tmp_path / "nonexistent.json") == {}

    def test_save_then_load(self, tmp_path: Path):
        path = tmp_path / "dedup.json"
        data = {"abc123": {"issue_number": 42, "count": 3}}
        _save_dedup(path, data)
        loaded = _load_dedup(path)
        assert loaded == data

    def test_load_corrupt_json(self, tmp_path: Path):
        path = tmp_path / "dedup.json"
        path.write_text("not json{{{", encoding="utf-8")
        assert _load_dedup(path) == {}


# ── GitHub API ───────────────────────────────────────────────────────────────


class TestCreateIssue:
    @patch("lorekeep.bugreport.urllib.request.urlopen")
    def test_success_returns_issue_number(self, mock_urlopen):
        mock_resp = BytesIO(json.dumps({"number": 99}).encode())
        mock_resp.status = 200  # type: ignore[attr-defined]
        mock_urlopen.return_value.__enter__ = lambda self: mock_resp
        mock_urlopen.return_value.__exit__ = lambda self, *a: False

        result = _create_github_issue("owner/repo", "token", "title", "body", ["bug"])
        assert result == 99
        mock_urlopen.assert_called_once()

    @patch("lorekeep.bugreport.urllib.request.urlopen")
    def test_http_error_returns_none(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "url", 403, "Forbidden", {}, None
        )
        result = _create_github_issue("owner/repo", "token", "title", "body", ["bug"])
        assert result is None

    @patch("lorekeep.bugreport.urllib.request.urlopen")
    def test_url_error_returns_none(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("no connection")
        result = _create_github_issue("owner/repo", "token", "title", "body", ["bug"])
        assert result is None


# ── issue body builder ───────────────────────────────────────────────────────


class TestIssueBody:
    def test_redacts_api_key(self):
        record = _make_record(msg="failed api_key=sk-super-secret-value")
        body = _build_issue_body(record, "run123")
        assert "super-secret" not in body
        assert "[REDACTED]" in body

    def test_contains_metadata(self):
        record = _make_record()
        body = _build_issue_body(record, "run123")
        assert "compile.chunk_failed" in body
        assert "ERROR" in body
        assert "run123" in body

    def test_traceback_is_redacted(self):
        record = _make_error_record()
        body = _build_issue_body(record, "run123")
        assert "bad chunk" not in body
        assert "[details redacted]" in body
        assert "ValueError" in body

    def test_redacts_home_directory(self):
        record = _make_record(msg=f"failed reading {Path.home()}/secret/file")
        body = _build_issue_body(record, "run123")
        assert str(Path.home()) not in body


# ── handler integration ──────────────────────────────────────────────────────


class TestHandlerEmit:
    def _setup_home(self, tmp_path: Path, monkeypatch, config_yaml: str = ""):
        """Set up an isolated LOREKEEP_HOME with optional config."""
        home = tmp_path / "home"
        home.mkdir()
        (home / "logs").mkdir()
        if config_yaml:
            (home / "config.yaml").write_text(config_yaml, encoding="utf-8")
        monkeypatch.setenv("LOREKEEP_HOME", str(home))
        # Reset the warn-once flag.
        import lorekeep.bugreport as br
        br._warned_no_token = False
        return home

    @patch("lorekeep.bugreport._create_github_issue")
    def test_creates_issue_on_error(self, mock_create, tmp_path: Path, monkeypatch):
        self._setup_home(tmp_path, monkeypatch)
        monkeypatch.setenv("LOREKEEP_GITHUB_TOKEN", "ghp_fake")
        mock_create.return_value = 42

        handler = BugReportHandler()
        record = _make_error_record()
        handler.emit(record)

        mock_create.assert_called_once()
        args = mock_create.call_args
        assert "manhhailua/lorekeep" in args.args[0]

    @patch("lorekeep.bugreport._create_github_issue")
    def test_ignores_warning(self, mock_create, tmp_path: Path, monkeypatch):
        self._setup_home(tmp_path, monkeypatch)
        monkeypatch.setenv("LOREKEEP_GITHUB_TOKEN", "ghp_fake")

        handler = BugReportHandler()
        record = _make_record(level=logging.WARNING)
        handler.emit(record)

        mock_create.assert_not_called()

    @patch("lorekeep.bugreport._create_github_issue")
    def test_ignores_info(self, mock_create, tmp_path: Path, monkeypatch):
        self._setup_home(tmp_path, monkeypatch)
        monkeypatch.setenv("LOREKEEP_GITHUB_TOKEN", "ghp_fake")

        handler = BugReportHandler()
        record = _make_record(level=logging.INFO)
        handler.emit(record)

        mock_create.assert_not_called()

    @patch("lorekeep.bugreport._create_github_issue")
    def test_skips_when_disabled(self, mock_create, tmp_path: Path, monkeypatch):
        self._setup_home(
            tmp_path,
            monkeypatch,
            config_yaml="bugreport:\n  enabled: false\n",
        )
        monkeypatch.setenv("LOREKEEP_GITHUB_TOKEN", "ghp_fake")

        handler = BugReportHandler()
        record = _make_error_record()
        handler.emit(record)

        mock_create.assert_not_called()

    @patch("lorekeep.bugreport._create_github_issue")
    def test_warns_once_without_token(self, mock_create, tmp_path: Path, monkeypatch, caplog):
        self._setup_home(tmp_path, monkeypatch)
        monkeypatch.delenv("LOREKEEP_GITHUB_TOKEN", raising=False)

        handler = BugReportHandler()

        with caplog.at_level(logging.WARNING, logger="lorekeep.bugreport"):
            handler.emit(_make_error_record())
        assert any("not set" in r.message for r in caplog.records)

        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="lorekeep.bugreport"):
            handler.emit(_make_error_record())
        assert not any("not set" in r.message for r in caplog.records)

        mock_create.assert_not_called()

    @patch("lorekeep.bugreport._create_github_issue")
    def test_dedup_prevents_duplicate(self, mock_create, tmp_path: Path, monkeypatch):
        self._setup_home(tmp_path, monkeypatch)
        monkeypatch.setenv("LOREKEEP_GITHUB_TOKEN", "ghp_fake")
        mock_create.return_value = 42

        handler = BugReportHandler()
        record = _make_error_record()

        handler.emit(record)  # first → creates issue
        handler.emit(record)  # second → dedup skip

        assert mock_create.call_count == 1

    @patch("lorekeep.bugreport._create_github_issue")
    def test_dedup_increments_count(self, mock_create, tmp_path: Path, monkeypatch):
        home = self._setup_home(tmp_path, monkeypatch)
        monkeypatch.setenv("LOREKEEP_GITHUB_TOKEN", "ghp_fake")
        mock_create.return_value = 42

        handler = BugReportHandler()
        record = _make_error_record()

        handler.emit(record)
        handler.emit(record)
        handler.emit(record)

        from lorekeep.bugreport import _dedup_path
        dedup = _load_dedup(_dedup_path())
        assert len(dedup) == 1
        entry = list(dedup.values())[0]
        assert entry["issue_number"] == 42
        assert entry["count"] == 3

    @patch("lorekeep.bugreport._create_github_issue")
    def test_network_failure_doesnt_crash(self, mock_create, tmp_path: Path, monkeypatch):
        self._setup_home(tmp_path, monkeypatch)
        monkeypatch.setenv("LOREKEEP_GITHUB_TOKEN", "ghp_fake")
        mock_create.return_value = None  # simulate failure

        handler = BugReportHandler()
        record = _make_error_record()

        handler.emit(record)  # should not raise

        from lorekeep.bugreport import _dedup_path
        dedup = _load_dedup(_dedup_path())
        assert len(dedup) == 0  # not recorded → allows retry next run


# ── CLI ──────────────────────────────────────────────────────────────────────


class TestCli:
    def test_bugreport_off(self, tmp_path: Path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        (home / "config.yaml").write_text(
            "bugreport:\n  enabled: true\n", encoding="utf-8"
        )
        monkeypatch.setenv("LOREKEEP_HOME", str(home))

        result = runner.invoke(app, ["bugreport", "off"])
        assert result.exit_code == 0, result.output
        assert "disabled" in result.output.lower()

        data = (home / "config.yaml").read_text(encoding="utf-8")
        assert "enabled: false" in data

    def test_bugreport_on(self, tmp_path: Path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        (home / "config.yaml").write_text(
            "bugreport:\n  enabled: false\n", encoding="utf-8"
        )
        monkeypatch.setenv("LOREKEEP_HOME", str(home))

        result = runner.invoke(app, ["bugreport", "on"])
        assert result.exit_code == 0, result.output
        assert "enabled" in result.output.lower()

        data = (home / "config.yaml").read_text(encoding="utf-8")
        assert "enabled: true" in data

    def test_bugreport_status(self, tmp_path: Path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        (home / "logs").mkdir()
        (home / "config.yaml").write_text(
            "bugreport:\n  enabled: true\n  repo: myorg/myrepo\n  token_env: MY_GH_TOKEN\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("LOREKEEP_HOME", str(home))
        monkeypatch.setenv("MY_GH_TOKEN", "ghp_x")

        result = runner.invoke(app, ["bugreport", "status"])
        assert result.exit_code == 0, result.output
        assert "myorg/myrepo" in result.output
        assert "token set: yes" in result.output
        assert "no errors reported yet" in result.output

    def test_bugreport_status_no_token(self, tmp_path: Path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        (home / "logs").mkdir()
        monkeypatch.setenv("LOREKEEP_HOME", str(home))
        monkeypatch.delenv("LOREKEEP_GITHUB_TOKEN", raising=False)

        result = runner.invoke(app, ["bugreport", "status"])
        assert result.exit_code == 0, result.output
        assert "token set: no" in result.output
