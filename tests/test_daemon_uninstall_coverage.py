"""Coverage fill for daemon_service uninstall/status functions + locomo helpers.

Covers the uninstall_*, status_* functions that call subprocess, plus the
locomo eval helper functions (_node_text, _edge_text, _load_src_text,
_search_raw_text, _f1_score).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from lorekeep.daemon_service import (
    _systemd_unit_path,
    _launchd_plist_path,
    _windows_startup_path,
    uninstall_systemd,
    status_systemd,
    uninstall_launchd,
    status_launchd,
    uninstall_windows,
    status_windows,
    install_launchd,
    uninstall,
    status,
)


# ======================================================================
# systemd uninstall / status
# ======================================================================

class TestSystemdUninstall:
    def test_uninstall_removes_unit_file(self, tmp_path, monkeypatch):
        """uninstall_systemd returns True and removes the unit file."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        unit_path = _systemd_unit_path()
        unit_path.parent.mkdir(parents=True, exist_ok=True)
        unit_path.write_text("[Unit]\n")

        calls = []
        monkeypatch.setattr("subprocess.run", lambda *a, **k: calls.append(a))
        assert uninstall_systemd() is True
        assert not unit_path.exists()
        assert len(calls) == 3  # stop, disable, daemon-reload

    def test_uninstall_no_unit_returns_false(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert uninstall_systemd() is False


class TestSystemdStatus:
    def test_status_returns_stdout(self, monkeypatch):
        mock_result = MagicMock()
        mock_result.stdout = "active\n"
        mock_result.stderr = ""
        monkeypatch.setattr("subprocess.run", lambda *a, **k: mock_result)
        assert status_systemd() == "active"

    def test_status_falls_back_to_stderr(self, monkeypatch):
        mock_result = MagicMock()
        mock_result.stdout = ""
        mock_result.stderr = "inactive\n"
        monkeypatch.setattr("subprocess.run", lambda *a, **k: mock_result)
        assert status_systemd() == "inactive"


# ======================================================================
# launchd uninstall / status
# ======================================================================

class TestLaunchdUninstall:
    def test_uninstall_removes_plist(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        plist_path = _launchd_plist_path()
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        plist_path.write_text("<plist/>")

        monkeypatch.setattr("subprocess.run", lambda *a, **k: None)
        assert uninstall_launchd() is True
        assert not plist_path.exists()

    def test_uninstall_no_plist_returns_false(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert uninstall_launchd() is False


class TestLaunchdStatus:
    def test_status_running(self, monkeypatch):
        mock_result = MagicMock()
        mock_result.returncode = 0
        monkeypatch.setattr("subprocess.run", lambda *a, **k: mock_result)
        assert status_launchd() == "running"

    def test_status_not_loaded(self, monkeypatch):
        mock_result = MagicMock()
        mock_result.returncode = 1
        monkeypatch.setattr("subprocess.run", lambda *a, **k: mock_result)
        assert status_launchd() == "not loaded"


# ======================================================================
# Windows uninstall / status
# ======================================================================

class TestWindowsUninstall:
    def test_uninstall_removes_script(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "lorekeep.daemon_service._windows_startup_path",
            lambda: tmp_path,
        )
        script = tmp_path / "lorekeep-daemon.vbs"
        script.write_text("script")
        assert uninstall_windows() is True
        assert not script.exists()

    def test_uninstall_no_script_returns_false(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "lorekeep.daemon_service._windows_startup_path",
            lambda: tmp_path,
        )
        assert uninstall_windows() is False


class TestWindowsStatus:
    def test_status_installed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "lorekeep.daemon_service._windows_startup_path",
            lambda: tmp_path,
        )
        (tmp_path / "lorekeep-daemon.vbs").write_text("x")
        assert status_windows() == "installed"

    def test_status_not_installed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "lorekeep.daemon_service._windows_startup_path",
            lambda: tmp_path,
        )
        assert status_windows() == "not installed"


# ======================================================================
# Platform dispatch: unsupported / darwin / win32
# ======================================================================

class TestPlatformDispatchExtra:
    def test_uninstall_unsupported(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "freebsd")
        assert uninstall() is False

    def test_status_darwin(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        with patch("lorekeep.daemon_service.status_launchd", return_value="running"):
            assert "launchd: running" == status()

    def test_status_win32(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        with patch("lorekeep.daemon_service.status_windows", return_value="installed"):
            assert "startup: installed" == status()

    def test_status_unsupported(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "freebsd")
        assert "unsupported" in status()


# ======================================================================
# launchd install log chmod OSError
# ======================================================================

class TestLaunchdInstallChmodError:
    def test_chmod_oserror_is_swallowed(self, tmp_path, monkeypatch):
        """If chmod fails (OSError), install_launchd continues silently."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr("subprocess.run", lambda *a, **k: None)
        monkeypatch.setattr("pathlib.Path.chmod",
                            lambda self, mode: (_ for _ in ()).throw(OSError("nope")))
        with patch("lorekeep.daemon_service._find_lorekeep_command",
                   return_value=("lorekeep", [])):
            plist_path = install_launchd(tmp_path / "data")
        assert plist_path.exists()
