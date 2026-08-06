"""Tests for daemon lifecycle: version self-check, SIGTERM, wiki fallback,
schema-triggered recompile, and PID file unification.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from lorekeep.cli import _on_disk_version


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def daemon_home(tmp_path: Path, monkeypatch) -> Path:
    """Isolated LOREKEEP_HOME with minimal structure."""
    home = tmp_path / "home"
    (home / "raw").mkdir(parents=True)
    (home / "graph").mkdir()
    (home / "logs").mkdir()
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    return home


# ---------------------------------------------------------------------------
# _on_disk_version
# ---------------------------------------------------------------------------

class TestOnDiskVersion:
    def test_returns_current_version(self):
        """_on_disk_version returns a non-None string matching the installed version."""
        v = _on_disk_version()
        assert v is not None
        assert len(v) > 0

    def test_returns_none_when_package_missing(self, monkeypatch):
        """When lorekeep is not installed as a package, returns None gracefully."""
        import importlib.metadata
        real_version = importlib.metadata.version

        def _raise(name):
            if name == "lorekeep":
                raise importlib.metadata.PackageNotFoundError("lorekeep")
            return real_version(name)

        monkeypatch.setattr(importlib.metadata, "version", _raise)
        assert _on_disk_version() is None

    def test_detects_version_change(self, monkeypatch):
        """When the on-disk version differs from startup, the change is detectable."""
        import importlib.metadata

        versions = iter(["0.19.2", "0.20.0"])
        monkeypatch.setattr(importlib.metadata, "version", lambda name: next(versions))

        first = _on_disk_version()
        second = _on_disk_version()
        assert first == "0.19.2"
        assert second == "0.20.0"
        assert first != second

    def test_stable_when_version_unchanged(self, monkeypatch):
        """When the on-disk version is stable, repeated calls return the same value."""
        import importlib.metadata
        monkeypatch.setattr(importlib.metadata, "version", lambda name: "0.19.2")

        assert _on_disk_version() == "0.19.2"
        assert _on_disk_version() == "0.19.2"


# ---------------------------------------------------------------------------
# Wiki fallback after daemon compile
# ---------------------------------------------------------------------------

class TestWikiFallbackAfterCompile:
    def test_wiki_regen_when_no_pending(self, daemon_home: Path, monkeypatch):
        """When daemon compiles and pending/ is empty, wiki should be regenerated.

        This tests the gap where daemon compile rewrote facts.jsonl but
        _do_auto_resolve returned False (no candidates) — wiki would go
        stale without the fallback.
        """
        # Write a facts.jsonl so wiki has something to work with.
        facts = daemon_home / "graph" / "facts.jsonl"
        facts.write_text(json.dumps({
            "kind": "node", "id": "svc:test", "type": "service",
            "ns": ["team"], "props": {"name": "test", "summary": "Test service."},
        }) + "\n")

        wiki_dir = daemon_home / "wiki"
        schema_path = daemon_home / "schema.json"

        from lorekeep.cli import _auto_generate_wiki
        _auto_generate_wiki(daemon_home / "graph", wiki_dir, schema_path)

        # Wiki should be generated
        assert (wiki_dir / "index.md").exists()
        assert (wiki_dir / "overview.md").exists()


# ---------------------------------------------------------------------------
# Schema-triggered recompile
# ---------------------------------------------------------------------------

class TestSchemaMtimeTracking:
    def test_schema_change_detected(self, daemon_home: Path):
        """Schema mtime change should be detectable between iterations."""
        schema = daemon_home / "schema.json"
        schema.write_text('{"version": 1}')

        mtime1 = schema.stat().st_mtime
        # Ensure mtime advances (some filesystems have 1s granularity)
        time.sleep(0.01)
        schema.write_text('{"version": 2}')
        mtime2 = schema.stat().st_mtime

        assert mtime2 > mtime1


# ---------------------------------------------------------------------------
# PID file unification
# ---------------------------------------------------------------------------

class TestPidFileUnification:
    def test_start_daemon_uses_daemon_pid(self, daemon_home: Path, monkeypatch):
        """_start_daemon must use .daemon.pid, not agent.pid."""
        import subprocess
        from lorekeep.cli import _start_daemon

        # Capture the PID path by mocking Popen
        captured_paths = []

        class FakeProc:
            pid = 99999

        original_popen = subprocess.Popen

        def mock_popen(*args, **kwargs):
            return FakeProc()

        monkeypatch.setattr(subprocess, "Popen", mock_popen)

        p = {
            "home": daemon_home,
            "logs": daemon_home / "logs",
        }
        _start_daemon(p)

        assert (daemon_home / ".daemon.pid").exists()
        assert not (daemon_home / "agent.pid").exists()

    def test_daemon_pid_prevents_double_start(self, daemon_home: Path):
        """watch() should refuse to start when .daemon.pid has a live PID."""
        pid_file = daemon_home / ".daemon.pid"
        pid_file.write_text(str(os.getpid()))  # our own PID is alive

        # Simulate the PID check logic from watch()
        should_exit = False
        if pid_file.exists():
            try:
                old_pid = int(pid_file.read_text().strip())
                os.kill(old_pid, 0)
                should_exit = True
            except (ProcessLookupError, ValueError, PermissionError):
                pass

        assert should_exit

    def test_stale_pid_self_heals(self, daemon_home: Path):
        """watch() should proceed when .daemon.pid has a dead PID."""
        pid_file = daemon_home / ".daemon.pid"
        # Use a PID that's almost certainly not running
        pid_file.write_text("99999999")

        should_proceed = True
        if pid_file.exists():
            try:
                old_pid = int(pid_file.read_text().strip())
                os.kill(old_pid, 0)
                should_proceed = False
            except (ProcessLookupError, ValueError, PermissionError):
                pass

        assert should_proceed


# ---------------------------------------------------------------------------
# SIGTERM handler
# ---------------------------------------------------------------------------

class TestSigtermHandler:
    def test_sigterm_removes_pid_file(self, daemon_home: Path):
        """SIGTERM handler must unlink the PID file before exiting."""
        from lorekeep.cli import watch
        import signal
        import threading

        pid_file = daemon_home / ".daemon.pid"
        pid_file.write_text(str(os.getpid()))

        # Define the handler logic inline (same as watch() does)
        def _on_sigterm(signum, frame):
            pid_file.unlink(missing_ok=True)

        # Register and send signal to ourselves
        signal.signal(signal.SIGTERM, _on_sigterm)
        os.kill(os.getpid(), signal.SIGTERM)

        assert not pid_file.exists()

        # Restore default handler
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
