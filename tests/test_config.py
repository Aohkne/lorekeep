from pathlib import Path

import pytest

from lorekeep.config import Config, load_config


def test_load_config(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "provider:\n"
        "  model: openai/gpt-4o-mini\n"
        "  api_base: null\n"
        "compile:\n"
        "  chunk_lines: 40\n"
    )
    c = load_config(cfg)
    assert isinstance(c, Config)
    assert c.provider.model == "openai/gpt-4o-mini"
    assert c.compile.chunk_lines == 40


def test_default_config_when_missing(tmp_path: Path):
    c = load_config(tmp_path / "missing.yaml")
    assert c.provider.model == "openai/gpt-4o-mini"
    assert c.provider.timeout_seconds == 120.0
    assert c.provider.max_retries == 2
    assert c.compile.chunk_lines == 60


def test_load_config_rejects_bare_model(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("provider:\n  model: deepseek-chat\n")
    with pytest.raises(ValueError) as ei:
        load_config(cfg)
    assert "deepseek/deepseek-chat" in str(ei.value)


def test_load_config_accepts_prefixed_model(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "provider:\n"
        "  model: deepseek/deepseek-chat\n"
        "  timeout_seconds: 45\n"
        "  max_retries: 1\n"
    )
    c = load_config(cfg)  # no raise
    assert c.provider.model == "deepseek/deepseek-chat"
    assert c.provider.timeout_seconds == 45
    assert c.provider.max_retries == 1


def test_load_config_drops_legacy_backend_silently(tmp_path: Path):
    """Old configs with `backend:` must still load (pydantic extra=ignore)."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "provider:\n"
        "  backend: openai\n"
        "  model: openai/gpt-4o-mini\n"
    )
    c = load_config(cfg)  # no raise, backend silently dropped
    assert c.provider.model == "openai/gpt-4o-mini"
    assert not hasattr(c.provider, "backend")


@pytest.mark.parametrize(
    "config",
    [
        "provider:\n  timeout_seconds: 0\n",
        "provider:\n  timeout_seconds: -1\n",
        "provider:\n  max_retries: -1\n",
    ],
)
def test_provider_resilience_config_rejects_invalid_values(tmp_path: Path, config: str):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(config)

    with pytest.raises(ValueError):
        load_config(cfg)


def test_build_provider_wires_timeout_and_retries(monkeypatch):
    from lorekeep import cli

    captured = {}

    class StubProvider:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(cli, "LiteLLMProvider", StubProvider)
    config = Config.model_validate({
        "provider": {
            "model": "deepseek/deepseek-chat",
            "timeout_seconds": 45,
            "max_retries": 1,
        },
    })

    cli._build_provider(config)

    assert captured["timeout_seconds"] == 45
    assert captured["max_retries"] == 1
