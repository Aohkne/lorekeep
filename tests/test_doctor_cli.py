import shutil
from pathlib import Path
from typer.testing import CliRunner

from lorekeep.cli import app
from lorekeep.compile.providers import FakeProvider

runner = CliRunner()


def _seed_graph(tmp_path: Path, fixtures: Path) -> Path:
    out = tmp_path / "graph"
    out.mkdir(exist_ok=True)
    shutil.copy(fixtures / "gold/payments.facts.jsonl", out / "facts.jsonl")
    return out


def test_doctor_ok(tmp_path: Path, fixtures: Path, monkeypatch):
    out = _seed_graph(tmp_path, fixtures)
    monkeypatch.setenv("LOREKEEP_OUT", str(out))
    monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))
    monkeypatch.setenv("LOREKEEP_READ_NS", "teams/backend")
    # Keep the provider ping offline — this test is about graph/schema/MCP only.
    monkeypatch.setattr("lorekeep.cli._has_provider", lambda c: False)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.stdout
    assert "all checks passed" in result.stdout.lower()


def test_doctor_missing_graph(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LOREKEEP_OUT", str(tmp_path / "nope"))
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "facts.jsonl not found" in result.stdout.lower()


def test_doctor_pings_provider_when_key_present(tmp_path: Path, fixtures: Path, monkeypatch):
    out = _seed_graph(tmp_path, fixtures)
    monkeypatch.setenv("LOREKEEP_OUT", str(out))
    monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))
    monkeypatch.setattr("lorekeep.cli._has_provider", lambda c: True)
    monkeypatch.setattr("lorekeep.cli._make_provider", lambda c: FakeProvider([]))
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.stdout
    assert "provider: ok" in result.stdout.lower()


def test_doctor_reports_auth_failure(tmp_path: Path, fixtures: Path, monkeypatch):
    out = _seed_graph(tmp_path, fixtures)
    monkeypatch.setenv("LOREKEEP_OUT", str(out))
    monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))

    class _BadAuth:
        def ping(self):
            raise Exception("401 Authentication Error")

    monkeypatch.setattr("lorekeep.cli._has_provider", lambda c: True)
    monkeypatch.setattr("lorekeep.cli._make_provider", lambda c: _BadAuth())
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "auth failed" in result.stdout.lower()


def test_doctor_reports_model_not_found(tmp_path: Path, fixtures: Path, monkeypatch):
    out = _seed_graph(tmp_path, fixtures)
    monkeypatch.setenv("LOREKEEP_OUT", str(out))
    monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))

    class _BadModel:
        def ping(self):
            raise Exception("NotFoundError: model not found")

    monkeypatch.setattr("lorekeep.cli._has_provider", lambda c: True)
    monkeypatch.setattr("lorekeep.cli._make_provider", lambda c: _BadModel())
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "model not found" in result.stdout.lower()


def test_doctor_skips_ping_when_no_key(tmp_path: Path, fixtures: Path, monkeypatch):
    out = _seed_graph(tmp_path, fixtures)
    monkeypatch.setenv("LOREKEEP_OUT", str(out))
    monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))
    monkeypatch.setattr("lorekeep.cli._has_provider", lambda c: False)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.stdout
    assert "provider: skipped" in result.stdout.lower()


def test_doctor_reports_bare_model_as_problem(tmp_path: Path, fixtures: Path, monkeypatch):
    """A bare model name is reported as a problem, not a crash."""
    out = _seed_graph(tmp_path, fixtures)
    cfg = tmp_path / "config.yaml"
    cfg.write_text("provider:\n  model: deepseek-chat\n")
    monkeypatch.setenv("LOREKEEP_OUT", str(out))
    monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))
    monkeypatch.setenv("LOREKEEP_CONFIG", str(cfg))
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "provider config" in result.stdout.lower()
    assert "deepseek/deepseek-chat" in result.stdout


def test_doctor_no_ping_env_escape_hatch(tmp_path: Path, fixtures: Path, monkeypatch):
    """LOREKEEP_DOCTOR_NO_PING=1 skips the ping even when a key is present."""
    out = _seed_graph(tmp_path, fixtures)
    monkeypatch.setenv("LOREKEEP_OUT", str(out))
    monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))
    monkeypatch.setenv("LOREKEEP_DOCTOR_NO_PING", "1")
    monkeypatch.setattr("lorekeep.cli._has_provider", lambda c: True)
    # If the ping ran, this would explode (network). It must NOT be called.
    def _boom(c):
        raise AssertionError("provider built despite LOREKEEP_DOCTOR_NO_PING=1")
    monkeypatch.setattr("lorekeep.cli._make_provider", _boom)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.stdout
    assert "ping skipped" in result.stdout.lower()


def test_doctor_reports_endpoint_unreachable(tmp_path: Path, fixtures: Path, monkeypatch):
    out = _seed_graph(tmp_path, fixtures)
    monkeypatch.setenv("LOREKEEP_OUT", str(out))
    monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))

    class _Unreachable:
        def ping(self):
            raise Exception("ConnectionError: Connection refused")

    monkeypatch.setattr("lorekeep.cli._has_provider", lambda c: True)
    monkeypatch.setattr("lorekeep.cli._make_provider", lambda c: _Unreachable())
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "endpoint unreachable" in result.stdout.lower()


class TestProviderPing:
    """ping() contract: FakeProvider returns OK without consuming the response
    queue (so compile tests' canned-response counts stay exact)."""

    def test_fake_ping_returns_ok(self):
        f = FakeProvider(["canned"])
        assert f.ping() == "OK"

    def test_fake_ping_does_not_consume_queue(self):
        f = FakeProvider(["canned"])
        assert f.ping() == "OK"
        assert f.extract_json("s", "u") == "canned"  # still there

    def test_fake_ping_works_with_empty_queue(self):
        assert FakeProvider([]).ping() == "OK"

    def test_litellm_provider_has_ping(self):
        from lorekeep.compile.providers import LiteLLMProvider
        assert callable(getattr(LiteLLMProvider, "ping", None))


class TestDoctorApiBaseHint:
    """api_base is redundant for native providers — doctor warns (non-fatal)."""

    def test_warns_api_base_on_native_provider(self, tmp_path: Path, fixtures: Path, monkeypatch):
        out = _seed_graph(tmp_path, fixtures)
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "provider:\n"
            "  model: dashscope/qwen-plus\n"
            "  api_base: https://dashscope-intl.aliyuncs.com/compatible-mode/v1\n"
        )
        monkeypatch.setenv("LOREKEEP_OUT", str(out))
        monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))
        monkeypatch.setenv("LOREKEEP_CONFIG", str(cfg))
        monkeypatch.setattr("lorekeep.cli._has_provider", lambda c: False)  # skip ping
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0, result.stdout
        assert "api_base" in result.stdout.lower()
        assert "dashscope" in result.stdout

    def test_no_api_base_hint_for_dynamic_ollama(self, tmp_path: Path, fixtures: Path, monkeypatch):
        out = _seed_graph(tmp_path, fixtures)
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "provider:\n"
            "  model: ollama/llama3\n"
            "  api_base: http://localhost:11434\n"
        )
        monkeypatch.setenv("LOREKEEP_OUT", str(out))
        monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))
        monkeypatch.setenv("LOREKEEP_CONFIG", str(cfg))
        monkeypatch.setattr("lorekeep.cli._has_provider", lambda c: False)
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0, result.stdout
        assert "api_base" not in result.stdout.lower()

    def test_openai_api_base_is_custom_endpoint_note(self, tmp_path: Path, fixtures: Path, monkeypatch):
        """openai/ + api_base is the OpenAI-compatible pattern, not a redundancy warning."""
        out = _seed_graph(tmp_path, fixtures)
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "provider:\n"
            "  model: openai/llama3.2\n"
            "  api_base: http://localhost:8000/v1\n"
        )
        monkeypatch.setenv("LOREKEEP_OUT", str(out))
        monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))
        monkeypatch.setenv("LOREKEEP_CONFIG", str(cfg))
        monkeypatch.setattr("lorekeep.cli._has_provider", lambda c: False)
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0, result.stdout
        assert "openai-compatible" in result.stdout.lower()
        assert "usually unnecessary" not in result.stdout.lower()


# ── agent connection + last session sections ─────────────────────────────────

def _doctor_base_env(tmp_path: Path, fixtures: Path, monkeypatch):
    """Shared setup: seed graph, skip provider ping."""
    out = _seed_graph(tmp_path, fixtures)
    monkeypatch.setenv("LOREKEEP_OUT", str(out))
    monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))
    monkeypatch.setattr("lorekeep.cli._has_provider", lambda c: False)
    return out


def test_doctor_shows_agent_section(tmp_path: Path, fixtures: Path, monkeypatch):
    """Doctor prints a compact agent table for installed agents."""
    _doctor_base_env(tmp_path, fixtures, monkeypatch)
    # Fake an installed + wired agent so the table has a row to show.
    monkeypatch.setattr(
        "lorekeep.cli._agent_report",
        lambda scope: [{
            "name": "grok",
            "label": "Grok Build",
            "installed": True,
            "session_data": True,
            "config": "/fake",
            "wired": True,
            "hook": None,
            "hooked": False,
            "ingest": ["transcript"],
        }],
    )
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.stdout
    assert "agents" in result.stdout.lower()
    assert "grok" in result.stdout
    assert "wired" in result.stdout.lower()


def test_doctor_shows_last_session(tmp_path: Path, fixtures: Path, monkeypatch):
    """Doctor reports the newest imported session per agent namespace."""
    out = _doctor_base_env(tmp_path, fixtures, monkeypatch)
    raw = tmp_path / "raw"
    grok_ns = raw / "grok-session"
    grok_ns.mkdir(parents=True)
    (grok_ns / "lorekeep-abc-001.md").write_text("# session")
    monkeypatch.setattr(
        "lorekeep.cli._agent_report", lambda scope: [],
    )
    # resolve_paths returns p["raw"]; it uses LOREKEEP_RAW or derives from home.
    monkeypatch.setenv("LOREKEEP_RAW", str(raw))
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.stdout
    assert "last session" in result.stdout.lower()
    assert "grok" in result.stdout
    assert "lorekeep-abc" in result.stdout


def test_doctor_no_raw_dir(tmp_path: Path, fixtures: Path, monkeypatch):
    """No raw/ → no session section, no crash."""
    _doctor_base_env(tmp_path, fixtures, monkeypatch)
    monkeypatch.setattr("lorekeep.cli._agent_report", lambda scope: [])
    monkeypatch.setenv("LOREKEEP_RAW", str(tmp_path / "nope-raw"))
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.stdout
    assert "last session" not in result.stdout.lower()


def test_doctor_shows_lifecycle_queue_state(
    tmp_path: Path, fixtures: Path, monkeypatch,
):
    """Queued and retrying hook events are visible for troubleshooting."""
    _doctor_base_env(tmp_path, fixtures, monkeypatch)
    home = tmp_path / "home"
    event_dir = home / "hook-events" / "cmd"
    event_dir.mkdir(parents=True)
    (event_dir / "cmd-session.json").write_text(
        '{"agent":"cmd","session_id":"s1","trigger":"turn_end_fallback",'
        '"attempts":0}\n'
    )
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    monkeypatch.setattr("lorekeep.cli._agent_report", lambda scope: [])

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0, result.stdout
    assert "lifecycle event queue" in result.stdout.lower()
    assert "waiting for idle" in result.stdout.lower()
