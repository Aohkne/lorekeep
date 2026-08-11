"""Tests for fatal-provider-error short-circuit in compile_graph.

When a chunk fails with a systemic error (AuthenticationError, ConnectionError,
etc.) the compile loop should abort immediately instead of trying every
remaining chunk — they will all fail identically.
"""
from __future__ import annotations

import json
from pathlib import Path

from lorekeep.compile.providers import FakeProvider
from lorekeep.models import Schema
from lorekeep.pipeline import _is_fatal_provider_error, compile_graph


def _copy_fixture(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(src.read_text())


def _load_schema(fixtures: Path) -> Schema:
    return Schema.load(json.loads((fixtures / "schema.json").read_text()))


# ---------------------------------------------------------------------------
# _is_fatal_provider_error unit tests
# ---------------------------------------------------------------------------

class TestIsFatalProviderError:
    def test_authentication_error_is_fatal(self):
        class AuthenticationError(Exception):
            pass
        assert _is_fatal_provider_error(AuthenticationError("bad key"))

    def test_connection_error_is_fatal(self):
        assert _is_fatal_provider_error(ConnectionError("refused"))

    def test_runtime_error_is_not_fatal(self):
        assert not _is_fatal_provider_error(RuntimeError("unexpected"))

    def test_value_error_is_not_fatal(self):
        assert not _is_fatal_provider_error(ValueError("bad json"))

    def test_wrapped_auth_error_is_fatal(self):
        """litellm wraps errors inside retry/tenacity — the cause chain must
        still be detected."""
        class AuthenticationError(Exception):
            pass

        try:
            try:
                raise AuthenticationError("bad key")
            except AuthenticationError:
                raise RuntimeError("retry exhausted")
        except RuntimeError as exc:
            assert _is_fatal_provider_error(exc)

    def test_timeout_is_fatal(self):
        class Timeout(Exception):
            pass
        assert _is_fatal_provider_error(Timeout("60s elapsed"))


# ---------------------------------------------------------------------------
# compile_graph short-circuit integration tests
# ---------------------------------------------------------------------------

class TestCompileShortCircuit:
    def test_auth_error_aborts_after_first_chunk(self, tmp_path: Path, fixtures: Path, caplog):
        """When the first chunk fails with AuthenticationError, compile must
        stop and NOT attempt remaining chunks."""
        import logging as _logging

        # Two markdown files → two chunks.
        raw = tmp_path / "raw"
        for name in ("payments.md", "auth.md"):
            _copy_fixture(fixtures / "raw/backend/payments.md",
                          raw / "teams/backend" / name)

        schema = _load_schema(fixtures)

        class _AuthFail(FakeProvider):
            call_count = 0

            def extract_json(self, system, user):
                _AuthFail.call_count += 1
                class AuthenticationError(Exception):
                    pass
                raise AuthenticationError("Invalid API key")

        provider = _AuthFail(responses=[])

        with caplog.at_level(_logging.ERROR, logger="lorekeep"):
            manifest = compile_graph(
                raw_root=raw, out_dir=tmp_path / "graph",
                schema=schema, provider=provider,
                cache_path=tmp_path / "cache.json", chunk_lines=60,
                max_workers=1,  # sequential to test deterministic short-circuit
            )

        # Only one chunk was attempted (short-circuit).
        assert _AuthFail.call_count == 1
        # Manifest records the error.
        assert manifest.errors
        assert manifest.node_count == 0
        # The abort event was logged.
        assert any(
            getattr(r, "event", "") == "compile.aborted_fatal"
            for r in caplog.records
        )

    def test_non_fatal_error_continues_all_chunks(self, tmp_path: Path, fixtures: Path):
        """RuntimeError (content-specific) must NOT short-circuit — the
        remaining chunks might succeed."""

        # Two markdown files → two chunks.
        raw = tmp_path / "raw"
        for name in ("payments.md", "auth.md"):
            _copy_fixture(fixtures / "raw/backend/payments.md",
                          raw / "teams/backend" / name)

        schema = _load_schema(fixtures)

        class _Boom(FakeProvider):
            call_count = 0

            def extract_json(self, system, user):
                _Boom.call_count += 1
                raise RuntimeError("unexpected content error")

        provider = _Boom(responses=[])

        manifest = compile_graph(
            raw_root=raw, out_dir=tmp_path / "graph",
            schema=schema, provider=provider,
            cache_path=tmp_path / "cache.json", chunk_lines=60,
        )

        # Both chunks were attempted (no short-circuit for non-fatal errors).
        assert _Boom.call_count == 2
        assert len(manifest.errors) == 2
