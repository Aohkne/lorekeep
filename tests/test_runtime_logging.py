from __future__ import annotations

import logging
import os
import re
import sys
import threading
from pathlib import Path

from lorekeep import output
from lorekeep.redaction import redact_text


def test_file_handler_writes_context_utc_and_redacted_traceback(tmp_path: Path):
    path = tmp_path / "logs" / "lorekeep.log"
    handler = output._make_file_handler(path)
    logger = logging.getLogger("lorekeep.test.runtime")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    try:
        raise RuntimeError("api_key=sk-super-secret-value")
    except RuntimeError:
        logger.exception("operation failed", extra={"event": "test.failed"})
    finally:
        handler.close()
        logger.handlers.clear()

    text = path.read_text(encoding="utf-8")
    assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", text)
    assert "event=test.failed" in text
    assert "run_id=" in text
    assert "RuntimeError: [details redacted]" in text
    assert "super-secret" not in text
    assert "\x1b[" not in text


def test_file_handler_rotation_and_private_permissions(tmp_path: Path):
    path = tmp_path / "logs" / "lorekeep.log"
    handler = output._make_file_handler(path)
    handler.maxBytes = 100
    logger = logging.getLogger("lorekeep.test.rotation")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    for _ in range(10):
        logger.info("x" * 80, extra={"event": "test.rotation"})
    handler.close()
    logger.handlers.clear()
    assert path.with_suffix(".log.1").exists()
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700


def test_redaction_covers_credentials_urls_and_home(tmp_path: Path):
    source = (
        f"api_key=abc123 password: hunter2 Authorization: Bearer secret-token "
        f"https://user:pass@example.test/a?token=qwerty path={tmp_path}/raw"
    )
    result = redact_text(source, home=tmp_path)
    for secret in ("abc123", "hunter2", "secret-token", "user:pass", "qwerty"):
        assert secret not in result
    assert "~/raw" in result


def test_redaction_preserves_harmless_metadata():
    value = "provider=openrouter model=openrouter/deepseek-chat count=12"
    assert redact_text(value) == value


def test_unhandled_exception_hook_logs_type_not_value(monkeypatch, caplog):
    previous_sys = sys.excepthook
    previous_thread = threading.excepthook
    previous_configured = output._exception_hooks_configured
    monkeypatch.setattr(output, "_exception_hooks_configured", False)
    try:
        output._install_exception_hooks()
        try:
            raise ValueError("raw-private-exception-content")
        except ValueError as exc:
            with caplog.at_level(logging.CRITICAL, logger="lorekeep.runtime"):
                output.sys.excepthook(type(exc), exc, exc.__traceback__)
    finally:
        sys.excepthook = previous_sys
        threading.excepthook = previous_thread
        output._exception_hooks_configured = previous_configured
    assert "unhandled exception error_type=ValueError" in caplog.text
    # The safe file formatter strips the value; caplog sees the original
    # record and therefore only assert the structured message itself here.
    assert caplog.records[-1].getMessage().endswith("ValueError")
