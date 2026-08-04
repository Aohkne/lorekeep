"""Conservative secret and path redaction for diagnostics.

Runtime logs intentionally contain operational metadata only.  This module is
the final safety net for exception messages and support artifacts, where a
third-party exception may still contain credentials.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|secret)"
    r"(\s*[=:]\s*)([^\s,;]+)"
)
_BEARER = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]+")
_URL_USERINFO = re.compile(r"(https?://)([^/@\s]+)@", re.IGNORECASE)
_URL_SECRET_QUERY = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|token|key|secret|password)=)[^&#\s]+"
)
_COMMON_KEY = re.compile(
    r"\b(?:sk|rk|pk|or|ds)-[A-Za-z0-9_-]{12,}\b",
    re.IGNORECASE,
)


def redact_text(value: object, *, home: Path | None = None) -> str:
    """Return a copy safe for logs and user-shareable diagnostics."""
    text = str(value)
    text = _SECRET_ASSIGNMENT.sub(r"\1\2[REDACTED]", text)
    text = _BEARER.sub(r"\1[REDACTED]", text)
    text = _URL_USERINFO.sub(r"\1[REDACTED]@", text)
    text = _URL_SECRET_QUERY.sub(r"\1[REDACTED]", text)
    text = _COMMON_KEY.sub("[REDACTED]", text)

    resolved_home = home
    if resolved_home is None:
        try:
            resolved_home = Path.home()
        except (RuntimeError, OSError):
            resolved_home = None
    if resolved_home:
        home_text = os.fspath(resolved_home)
        if home_text and home_text != os.path.sep:
            text = text.replace(home_text, "~")
    return text
