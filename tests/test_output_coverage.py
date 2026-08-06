"""Coverage fill for output.py uncovered branches.

Tests:
- _NullProgress: __bool__, advance, update
- progress(): non-tty context manager path
- status(): non-tty context manager path
- configure_logging(): OSError handler paths
- _SafeFileFormatter.formatException: traceback redaction
"""
from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from rich.console import Console

from lorekeep import output
from lorekeep.output import (
    _NullProgress,
    _ProgressHandle,
    _SafeFileFormatter,
    configure_logging,
    is_quiet,
    is_terminal,
    progress,
    status,
)


# ── _NullProgress ───────────────────────────────────────────────────────────

class TestNullProgress:
    def test_is_falsy(self):
        assert not _NullProgress()

    def test_advance_noop(self):
        p = _NullProgress()
        p.advance(5)  # must not raise
        p.advance()   # default arg

    def test_update_noop(self):
        p = _NullProgress()
        p.update(completed=10, total=100)
        p.update()
        p.update(completed=None, total=None)


# ── progress() non-tty ──────────────────────────────────────────────────────

class TestProgressNonTty:
    """Under CliRunner / non-tty, progress yields a falsy _NullProgress."""

    def test_non_tty_yields_null_progress(self, monkeypatch):
        """Force non-tty to exercise the _NullProgress path."""
        monkeypatch.setattr(Console, "is_terminal", PropertyMock(return_value=False))
        assert not is_terminal()
        with progress("compiling") as handle:
            assert not handle
            assert isinstance(handle, _NullProgress)

    def test_non_tty_advance_update_silent(self, monkeypatch):
        monkeypatch.setattr(Console, "is_terminal", PropertyMock(return_value=False))
        with progress("compiling", total=100) as handle:
            handle.advance(10)
            handle.update(completed=50, total=200)


# ── status() non-tty ────────────────────────────────────────────────────────

class TestStatusNonTty:
    def test_non_tty_prints_label(self, monkeypatch, capsys):
        monkeypatch.setattr(Console, "is_terminal", PropertyMock(return_value=False))
        with status("processing"):
            pass
        captured = capsys.readouterr()
        assert "processing" in captured.out

    def test_non_tty_executes_body(self, monkeypatch):
        monkeypatch.setattr(Console, "is_terminal", PropertyMock(return_value=False))
        flag = False
        with status("working"):
            flag = True
        assert flag


# ── configure_logging error paths ───────────────────────────────────────────

class TestConfigureLoggingErrorPaths:
    """configure_logging must survive OSError from file handler creation."""

    @pytest.fixture(autouse=True)
    def _reset_logging(self):
        """Reset module-level globals so each test starts clean."""
        output._logging_configured = False
        output._file_handler = None
        output._bugreport_handler = None
        yield
        # cleanup: remove handlers we added
        lk = logging.getLogger("lorekeep")
        for h in list(lk.handlers):
            lk.removeHandler(h)
        root = logging.getLogger()
        for h in list(root.handlers):
            if hasattr(h, "_lorekeep_test"):
                root.removeHandler(h)

    def test_file_handler_oserror_swallowed(self, monkeypatch):
        """_make_file_handler raising OSError → logging still configured."""
        def _boom(path):
            raise OSError("read-only filesystem")
        monkeypatch.setattr(output, "_make_file_handler", _boom)
        # Must NOT raise
        configure_logging(logging.INFO)
        assert output._logging_configured is True

    def test_bugreport_handler_exception_swallowed(self, monkeypatch, tmp_path):
        """BugReportHandler failing → debug-logged, not raised."""
        # Use a real file handler so logging internals don't break
        from logging import FileHandler
        real_handler = FileHandler(tmp_path / "test.log")
        monkeypatch.setattr(output, "_make_file_handler", lambda p: real_handler)

        # Simulate bugreport import failing inside configure_logging
        import lorekeep.output as out_mod
        original = out_mod.BugReportHandler if hasattr(out_mod, 'BugReportHandler') else None

        def _patched_init(self, *a, **kw):
            raise ImportError("no bugreport module")

        # Patch BugReportHandler.__init__ to raise
        from lorekeep.bugreport import BugReportHandler
        monkeypatch.setattr(BugReportHandler, "__init__", _patched_init)

        configure_logging(logging.DEBUG)
        # Handler is None (creation failed) but logging still configured
        assert output._logging_configured is True
        real_handler.close()


# ── _SafeFileFormatter.formatException ──────────────────────────────────────

class TestSafeFileFormatter:
    def test_traceback_redacts_value(self):
        """formatException keeps frames but redacts exception value."""
        fmt = _SafeFileFormatter("%(message)s")
        try:
            raise ValueError("SECRET_API_KEY=abc123")
        except ValueError:
            result = fmt.formatException(sys.exc_info())

        # The exception VALUE is redacted — the final line shows type only
        assert "details redacted" in result
        assert "ValueError" in result
        # The exception message must NOT appear in the redacted summary line
        lines = result.strip().split("\n")
        last_line = lines[-1]
        assert "SECRET_API_KEY" not in last_line

    def test_format_applies_redaction(self):
        """Full format() redacts through redact_text — common key patterns."""
        fmt = _SafeFileFormatter("event=test %(message)s")
        # Use a token long enough to match _COMMON_KEY (12+ chars after prefix)
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="api_key=supersecret123 token=sk-abcdefghijklmno", args=(), exc_info=None,
        )
        result = fmt.format(record)
        assert "supersecret123" not in result
        assert "sk-abcdefghijklmno" not in result
        assert "[REDACTED]" in result


# ── is_quiet / level wiring ─────────────────────────────────────────────────

class TestQuietWiring:
    @pytest.fixture(autouse=True)
    def _reset(self):
        output._logging_configured = False
        output._file_handler = None
        output._bugreport_handler = None
        yield
        lk = logging.getLogger("lorekeep")
        for h in list(lk.handlers):
            lk.removeHandler(h)

    def test_warning_level_sets_quiet(self, monkeypatch, tmp_path):
        from logging import FileHandler
        fh = FileHandler(tmp_path / "test.log")
        monkeypatch.setattr(output, "_make_file_handler", lambda p: fh)
        configure_logging(logging.WARNING)
        assert is_quiet() is True
        fh.close()

    def test_info_level_not_quiet(self, monkeypatch, tmp_path):
        from logging import FileHandler
        fh = FileHandler(tmp_path / "test.log")
        monkeypatch.setattr(output, "_make_file_handler", lambda p: fh)
        configure_logging(logging.INFO)
        assert is_quiet() is False
        fh.close()

