"""Laputa CLI."""
from __future__ import annotations

import json
import os
from pathlib import Path

import typer

from laputa import __version__
from laputa.compile.providers import FakeProvider, LiteLLMProvider
from laputa.config import load_config
from laputa.pipeline import compile_graph
from laputa.schema_io import load_schema

app = typer.Typer(help="Laputa — compile team docs into a temporal knowledge graph.")


# Empty callback forces multi-command mode so subcommands are not auto-promoted.
@app.callback()
def _main() -> None:
    """Laputa — compile team docs into a temporal knowledge graph."""


def _paths() -> dict[str, Path]:
    return {
        "raw": Path(os.environ.get("LAPUTA_RAW", "raw")),
        "out": Path(os.environ.get("LAPUTA_OUT", "graph")),
        "cache": Path(os.environ.get("LAPUTA_CACHE", ".laputa/cache.json")),
        "schema": Path(os.environ.get("LAPUTA_SCHEMA", "graph/schema.json")),
        "config": Path(os.environ.get("LAPUTA_CONFIG", ".laputa/config.yaml")),
    }


@app.command()
def version() -> None:
    """Print the Laputa version."""
    typer.echo(f"laputa {__version__}")


@app.command()
def compile() -> None:
    """Compile raw/ into graph/facts.jsonl."""
    p = _paths()
    schema = load_schema(p["schema"])
    config = load_config(p["config"])

    if os.environ.get("LAPUTA_PROVIDER") == "fake":
        canned = json.dumps({
            "nodes": [
                {"id": "svc:payments-api", "type": "service", "name": "payments-api",
                 "props": {"lang": "go"}, "valid_from": "2024-01-15"},
                {"id": "svc:auth", "type": "service", "name": "auth"},
                {"id": "team:backend", "type": "team", "name": "team-backend"},
                {"id": "dec:adr-007", "type": "decision", "name": "adr-007",
                 "props": {"title": "payments-api adopts internal signing"}},
            ],
            "edges": [
                {"type": "depends_on", "from": "svc:payments-api", "to": "svc:auth",
                 "valid_from": "2024-01-15", "valid_to": "2025-03-01"},
                {"type": "decided_by", "from": "dec:adr-007", "to": "team:backend"},
            ],
            "aliases": {},
        })
        provider = FakeProvider(responses=[canned])
    else:
        provider = LiteLLMProvider(
            model=config.provider.model,
            api_base=config.provider.api_base,
            temperature=config.provider.temperature,
        )

    manifest = compile_graph(
        raw_root=p["raw"], out_dir=p["out"], schema=schema,
        provider=provider, cache_path=p["cache"], chunk_lines=config.compile.chunk_lines,
    )
    typer.echo(f"compiled: {manifest.node_count} nodes, {manifest.edge_count} edges, "
               f"run_id={manifest.run_id}, facts_hash={manifest.facts_hash}")


if __name__ == "__main__":
    app()
