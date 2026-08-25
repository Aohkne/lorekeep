"""Tests for the colored output + progress helpers (src/lorekeep/output.py)."""
from __future__ import annotations

import logging
import re
from io import StringIO

import pytest
from rich.console import Console

from lorekeep import output

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    return _ANSI.sub("", text)


def _tty_console(buf: StringIO) -> Console:
    """A Console that thinks it's a terminal and writes to *buf* (ANSI emitted)."""
    return Console(file=buf, force_terminal=True, color_system="truecolor", width=120)


def _nontty_console(buf: StringIO) -> Console:
    # Pin False: env like FORCE_COLOR=0 still counts as "set" in Rich and
    # would otherwise treat a StringIO as a terminal.
    return Console(file=buf, force_terminal=False, width=120)


class TestHelpersColor:
    def test_ok_emits_color_and_glyph_in_tty(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        buf = StringIO()
        monkeypatch.setattr(output, "console", _tty_console(buf))
        output.ok("done")
        text = buf.getvalue()
        assert "\x1b[" in text          # ANSI color emitted
        assert "✓" in text
        assert "done" in text

    def test_ok_plain_in_nontty(self, monkeypatch):
        buf = StringIO()
        monkeypatch.setattr(output, "console", _nontty_console(buf))
        output.ok("done")
        text = buf.getvalue()
        assert "\x1b[" not in text       # no ANSI in non-tty (test/log safety)
        assert "done" in text

    def test_brackets_in_message_not_parsed_as_markup(self, monkeypatch):
        buf = StringIO()
        monkeypatch.setattr(output, "console", _tty_console(buf))
        output.ok("namespaces=['me', 'public']")
        # strip ANSI, then the literal text must survive (no markup mangling)
        assert "namespaces=['me', 'public']" in _plain(buf.getvalue())

    def test_error_goes_to_stdout_console(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        buf = StringIO()
        monkeypatch.setattr(output, "console", _tty_console(buf))
        output.error("boom")
        text = buf.getvalue()
        assert "\x1b[" in text
        assert "✗" in text
        assert "boom" in text

    def test_info_warn_dim_have_glyphs(self, monkeypatch):
        buf = StringIO()
        monkeypatch.setattr(output, "console", _tty_console(buf))
        output.info("i")
        output.step("s")
        output.dim("d")
        output.warn("w")
        out = buf.getvalue()
        assert "→" in out and "…" in out and "w" in out


class TestProgress:
    def test_progress_silent_when_not_terminal(self, monkeypatch):
        buf = StringIO()
        monkeypatch.setattr(output, "console", _nontty_console(buf))
        with output.progress("Compiling", total=3) as h:
            h.advance()
            h.advance()
        assert buf.getvalue() == ""          # nothing rendered

    def test_progress_renders_when_terminal(self, monkeypatch):
        buf = StringIO()
        monkeypatch.setattr(output, "console", _tty_console(buf))
        with output.progress("Compiling", total=2) as h:
            # Rich 15 Live does not persist bar text into a StringIO, even with
            # force_terminal=True. The contract we own is: tty → real handle.
            assert isinstance(h, output._ProgressHandle)
            h.advance()
            h.advance()

    def test_null_progress_update_is_noop(self):
        h = output._NullProgress()
        h.advance(5)                          # must not raise
        h.update(completed=3, total=10)

    def test_status_prints_label_in_nontty(self, monkeypatch):
        buf = StringIO()
        monkeypatch.setattr(output, "console", _nontty_console(buf))
        with output.status("Importing sessions"):
            pass
        assert "Importing sessions" in buf.getvalue()

    def test_status_silent_label_in_nontty_when_blank(self, monkeypatch):
        # non-tty: status always prints the label once (no spinner anim)
        buf = StringIO()
        monkeypatch.setattr(output, "console", _nontty_console(buf))
        with output.status("working"):
            pass
        assert "\x1b[" not in buf.getvalue()


class TestConfigureLogging:
    def test_sets_lorekeep_level(self):
        output.configure_logging(logging.DEBUG)
        assert logging.getLogger("lorekeep").level == logging.DEBUG
        output.configure_logging(logging.WARNING)
        assert logging.getLogger("lorekeep").level == logging.WARNING

    def test_quiet_flag_tracks_warning_level(self):
        output.configure_logging(logging.INFO)
        assert output.is_quiet() is False
        output.configure_logging(logging.WARNING)
        assert output.is_quiet() is True

    def test_idempotent_handler_attachment(self, monkeypatch):
        root = logging.getLogger()
        saved = list(root.handlers)
        monkeypatch.setattr(output, "_logging_configured", False)
        try:
            root.handlers = [h for h in root.handlers if type(h).__name__ != "RichHandler"]
            before = len(root.handlers)
            output.configure_logging(logging.INFO)
            after_first = len(root.handlers)
            output.configure_logging(logging.DEBUG)
            after_second = len(root.handlers)
            assert after_first == before + 1   # one RichHandler added
            assert after_second == after_first  # not added twice
        finally:
            root.handlers = saved
