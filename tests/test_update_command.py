"""Tests for `lorekeep update` command and install-method detection.

Covers:
- _detect_install_method: uv, pipx, pip, unknown
- _latest_pypi_version: network success (mocked), network failure
- update --check: prints versions, no upgrade
- update already latest: prints "up to date"
- update with upgrade available: runs correct command per method
- update --force: reinstall even when versions match
- update daemon restart after upgrade
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from lorekeep.cli import app, _detect_install_method, _latest_pypi_version

runner = CliRunner()


# ── _detect_install_method ─────────────────────────────────────────────────

class TestDetectInstallMethod:
    def test_detects_uv(self, monkeypatch):
        """uv tool install path is detected."""
        fake_exe = "/home/user/.local/share/uv/tools/lorekeep/bin/python3"
        monkeypatch.setattr(sys, "executable", fake_exe)
        assert _detect_install_method() == "uv"

    def test_detects_pipx(self, monkeypatch):
        """pipx is detected when on PATH and not a uv install."""
        fake_exe = "/usr/bin/python3"
        monkeypatch.setattr(sys, "executable", fake_exe)
        monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/pipx" if cmd == "pipx" else None)
        assert _detect_install_method() == "pipx"

    def test_detects_pip(self, monkeypatch):
        """pip is detected when pip module is importable."""
        import shutil
        fake_exe = "/usr/bin/python3"
        monkeypatch.setattr(sys, "executable", fake_exe)
        monkeypatch.setattr(shutil, "which", lambda cmd: None)

        # Mock the pip import check to succeed
        import builtins
        real_import = builtins.__import__
        def mock_import(name, *args, **kwargs):
            if name == "pip":
                return MagicMock()
            return real_import(name, *args, **kwargs)
        monkeypatch.setattr(builtins, "__import__", mock_import)
        assert _detect_install_method() == "pip"

    def test_unknown(self, monkeypatch):
        """Returns 'unknown' when no package manager found."""
        fake_exe = "/usr/local/python3"
        monkeypatch.setattr(sys, "executable", fake_exe)
        monkeypatch.setattr("shutil.which", lambda cmd: None)

        # Mock the pip import check to fail
        import builtins
        real_import = builtins.__import__
        def mock_import(name, *args, **kwargs):
            if name == "pip":
                raise ImportError("No module named 'pip'")
            return real_import(name, *args, **kwargs)
        monkeypatch.setattr(builtins, "__import__", mock_import)
        assert _detect_install_method() == "unknown"


# ── _latest_pypi_version ───────────────────────────────────────────────────

class TestLatestPyPIVersion:
    def test_returns_version_on_success(self, monkeypatch):
        """Returns version string from PyPI JSON."""
        import io
        import json

        payload = json.dumps({"info": {"version": "9.9.9"}}).encode()
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read = MagicMock(return_value=payload)

        monkeypatch.setattr(
            "urllib.request.urlopen", lambda url, timeout: mock_resp
        )
        assert _latest_pypi_version() == "9.9.9"

    def test_returns_none_on_failure(self, monkeypatch):
        """Returns None when network fails."""
        def raise_timeout(*args, **kwargs):
            raise ConnectionError("timeout")
        monkeypatch.setattr("urllib.request.urlopen", raise_timeout)
        assert _latest_pypi_version() is None


# ── update --check ─────────────────────────────────────────────────────────

class TestUpdateCheck:
    def test_check_shows_versions(self, monkeypatch):
        """--check prints current + latest without upgrading."""
        monkeypatch.setattr("lorekeep.cli._latest_pypi_version", lambda: "99.0.0")
        monkeypatch.setattr("lorekeep.cli.__version__", "1.0.0")
        result = runner.invoke(app, ["update", "--check"])
        assert result.exit_code == 0
        assert "1.0.0" in result.stdout
        assert "99.0.0" in result.stdout
        assert "update available" in result.stdout.lower()

    def test_check_already_latest(self, monkeypatch):
        """--check when up to date shows 'already up to date'."""
        monkeypatch.setattr("lorekeep.cli._latest_pypi_version", lambda: "1.0.0")
        monkeypatch.setattr("lorekeep.cli.__version__", "1.0.0")
        result = runner.invoke(app, ["update", "--check"])
        assert result.exit_code == 0
        assert "up to date" in result.stdout.lower()

    def test_check_no_network(self, monkeypatch):
        """--check fails gracefully when PyPI unreachable."""
        monkeypatch.setattr("lorekeep.cli._latest_pypi_version", lambda: None)
        result = runner.invoke(app, ["update", "--check"])
        assert result.exit_code == 1
        assert "pypi" in result.stdout.lower() or "network" in result.stdout.lower()


# ── update (upgrade) ───────────────────────────────────────────────────────

class TestUpdateUpgrade:
    def test_already_latest_no_upgrade(self, monkeypatch):
        """When versions match and no --force, exits 0 without upgrading."""
        monkeypatch.setattr("lorekeep.cli._latest_pypi_version", lambda: "1.0.0")
        monkeypatch.setattr("lorekeep.cli.__version__", "1.0.0")
        call_count = []
        def fake_run(*a, **kw):
            call_count.append(True)
            return MagicMock(returncode=0)
        monkeypatch.setattr("subprocess.run", fake_run)
        result = runner.invoke(app, ["update"])
        assert result.exit_code == 0
        assert "up to date" in result.stdout.lower()
        assert len(call_count) == 0  # no upgrade command was run

    def test_upgrades_via_uv(self, monkeypatch):
        """Detects uv install and runs uv tool upgrade."""
        monkeypatch.setattr("lorekeep.cli._latest_pypi_version", lambda: "99.0.0")
        monkeypatch.setattr("lorekeep.cli.__version__", "1.0.0")
        monkeypatch.setattr("lorekeep.cli._detect_install_method", lambda: "uv")

        captured_cmd = []
        def fake_run(cmd, **kwargs):
            captured_cmd.append(cmd)
            return MagicMock(returncode=0)
        monkeypatch.setattr("subprocess.run", fake_run)
        monkeypatch.setattr("lorekeep.cli._on_disk_version", lambda: "99.0.0")
        monkeypatch.setattr("lorekeep.cli._daemon_pid", lambda p: None)

        result = runner.invoke(app, ["update"])
        assert result.exit_code == 0
        assert len(captured_cmd) == 1
        assert captured_cmd[0] == ["uv", "tool", "upgrade", "lorekeep"]

    def test_upgrades_via_pip(self, monkeypatch):
        """Detects pip install and runs pip install --upgrade."""
        monkeypatch.setattr("lorekeep.cli._latest_pypi_version", lambda: "99.0.0")
        monkeypatch.setattr("lorekeep.cli.__version__", "1.0.0")
        monkeypatch.setattr("lorekeep.cli._detect_install_method", lambda: "pip")

        captured_cmd = []
        def fake_run(cmd, **kwargs):
            captured_cmd.append(cmd)
            return MagicMock(returncode=0)
        monkeypatch.setattr("subprocess.run", fake_run)
        monkeypatch.setattr("lorekeep.cli._on_disk_version", lambda: "99.0.0")
        monkeypatch.setattr("lorekeep.cli._daemon_pid", lambda p: None)

        result = runner.invoke(app, ["update"])
        assert result.exit_code == 0
        assert len(captured_cmd) == 1
        assert "install" in captured_cmd[0]
        assert "--upgrade" in captured_cmd[0]
        assert "lorekeep" in captured_cmd[0]

    def test_unknown_install_errors(self, monkeypatch):
        """Unknown install method exits 1 with manual instructions."""
        monkeypatch.setattr("lorekeep.cli._latest_pypi_version", lambda: "99.0.0")
        monkeypatch.setattr("lorekeep.cli.__version__", "1.0.0")
        monkeypatch.setattr("lorekeep.cli._detect_install_method", lambda: "unknown")

        result = runner.invoke(app, ["update"])
        assert result.exit_code == 1
        assert "manual" in result.stdout.lower()

    def test_force_reinstall(self, monkeypatch):
        """--force upgrades even when versions match."""
        monkeypatch.setattr("lorekeep.cli._latest_pypi_version", lambda: "1.0.0")
        monkeypatch.setattr("lorekeep.cli.__version__", "1.0.0")
        monkeypatch.setattr("lorekeep.cli._detect_install_method", lambda: "uv")

        captured_cmd = []
        def fake_run(cmd, **kwargs):
            captured_cmd.append(cmd)
            return MagicMock(returncode=0)
        monkeypatch.setattr("subprocess.run", fake_run)
        monkeypatch.setattr("lorekeep.cli._on_disk_version", lambda: "1.0.0")
        monkeypatch.setattr("lorekeep.cli._daemon_pid", lambda p: None)

        result = runner.invoke(app, ["update", "--force"])
        assert result.exit_code == 0
        assert len(captured_cmd) == 1
        assert "--force" in captured_cmd[0]

    def test_upgrade_failure_exit_code(self, monkeypatch):
        """Upgrade command failure propagates exit code."""
        monkeypatch.setattr("lorekeep.cli._latest_pypi_version", lambda: "99.0.0")
        monkeypatch.setattr("lorekeep.cli.__version__", "1.0.0")
        monkeypatch.setattr("lorekeep.cli._detect_install_method", lambda: "uv")
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: MagicMock(returncode=1))
        result = runner.invoke(app, ["update"])
        assert result.exit_code == 1
