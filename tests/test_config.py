from pathlib import Path
from lorekeep.config import Config, load_config


def test_load_config(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "provider:\n"
        "  backend: openai\n"
        "  model: gpt-4o-mini\n"
        "  api_base: null\n"
        "compile:\n"
        "  chunk_lines: 40\n"
    )
    c = load_config(cfg)
    assert isinstance(c, Config)
    assert c.provider.model == "gpt-4o-mini"
    assert c.compile.chunk_lines == 40


def test_default_config_when_missing(tmp_path: Path):
    c = load_config(tmp_path / "missing.yaml")
    assert c.provider.backend == "openai"
    assert c.compile.chunk_lines == 60
