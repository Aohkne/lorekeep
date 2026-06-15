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
                {"id": "dec:adr-007", "type": "decision",
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
        api_key = None
        if config.provider.api_key_env:
            api_key = os.environ.get(config.provider.api_key_env)
        if not api_key:
            api_key = config.provider.api_key
        provider = LiteLLMProvider(
            model=config.provider.model,
            api_base=config.provider.api_base,
            temperature=config.provider.temperature,
            api_key=api_key,
        )

    manifest = compile_graph(
        raw_root=p["raw"], out_dir=p["out"], schema=schema,
        provider=provider, cache_path=p["cache"], chunk_lines=config.compile.chunk_lines,
    )
    typer.echo(f"compiled: {manifest.node_count} nodes, {manifest.edge_count} edges, "
               f"run_id={manifest.run_id}, facts_hash={manifest.facts_hash}")


@app.command(name="eval")
def eval_cmd() -> None:
    """Run Tier-1 construction-quality evaluation vs the gold corpus."""
    p = _paths()
    gold_dir = Path(os.environ.get("LAPUTA_GOLD", "tests/fixtures/gold"))
    from laputa.eval.construction import extraction_report, structure_report
    report = {
        "extraction": extraction_report(p["out"], gold_dir),
        "structure": structure_report(p["out"]),
    }
    results_path = Path(os.environ.get("LAPUTA_EVAL_RESULTS",
                                       ".laputa/eval/results.json"))
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(json.dumps(report, indent=2, sort_keys=True))
    typer.echo(json.dumps(report, indent=2, sort_keys=True))


@app.command()
def check() -> None:
    """Validate the compiled graph: loads, no dangling edges."""
    p = _paths()
    from laputa.eval.construction import structure_report
    struct = structure_report(p["out"])
    if struct["dangling_edge_rate"] > 0:
        typer.echo(f"check: FAIL — {struct['dangling_edge_rate']} dangling edges")
        raise typer.Exit(code=1)
    typer.echo(f"check: ok — {struct['node_count']} nodes, {struct['edge_count']} edges, 0 dangling")


@app.command()
def serve(
    transport: str = typer.Option("stdio", "--transport", help="stdio (default) | http"),
) -> None:
    """Serve the scoped graph over MCP."""
    p = _paths()
    raw_ns = os.environ.get("LAPUTA_NS")
    if raw_ns:
        allowed = [x.strip() for x in raw_ns.split(",") if x.strip()]
    else:
        allowed = load_config(p["config"]).ns.default
    from laputa.mcp_server import configure, mcp
    configure(graph_dir=p["out"], allowed_ns=allowed, schema_path=p["schema"])
    mcp.run(transport=transport)


mcp_app = typer.Typer(help="Coding-agent integration.")
app.add_typer(mcp_app, name="mcp")


@mcp_app.command("add")
def mcp_add(
    agent: str = typer.Option(..., "--agent", help="claude | cursor | codex"),
    scope: str = typer.Option("project", "--scope", help="project | user"),
    ns: str = typer.Option(None, "--ns", help="namespace to scope the agent to"),
) -> None:
    """Write the agent's MCP config + print an agent-memory snippet."""
    from laputa.integrations import claude_code, codex, cursor
    from laputa.integrations.common import agent_memory_snippet, resolve_command

    p = _paths()
    config = load_config(p["config"])
    command, args = resolve_command(config.install_source)

    target = Path.cwd() if scope == "project" else Path.home()
    writers = {"claude": claude_code, "cursor": cursor, "codex": codex}
    if agent not in writers:
        typer.echo(f"unknown agent: {agent} (choose claude|cursor|codex)")
        raise typer.Exit(code=1)
    written = writers[agent].write_config(target, command, args, ns)
    typer.echo(f"wrote {agent} config -> {written}")
    typer.echo("\n" + agent_memory_snippet())


@app.command()
def doctor() -> None:
    """Verify install: graph loads, schema valid, ns resolves, a tool responds."""
    p = _paths()
    problems = []

    facts_path = p["out"] / "facts.jsonl"
    if not facts_path.exists():
        typer.echo(f"FAIL: facts.jsonl not found at {facts_path}")
        raise typer.Exit(code=1)

    try:
        from laputa.store.graph import GraphStore
        store = GraphStore.from_jsonl(facts_path)
    except Exception as exc:
        typer.echo(f"FAIL: cannot load graph: {exc}")
        raise typer.Exit(code=1)

    if not p["schema"].exists():
        problems.append("schema.json missing")
    else:
        try:
            load_schema(p["schema"])
        except Exception as exc:
            problems.append(f"schema invalid: {exc}")

    raw_ns = os.environ.get("LAPUTA_NS")
    allowed = [x.strip() for x in raw_ns.split(",")] if raw_ns else load_config(p["config"]).ns.default

    try:
        from laputa.mcp_server import configure, list_namespaces
        configure(graph_dir=p["out"], allowed_ns=allowed, schema_path=p["schema"])
        ns = list_namespaces()
    except Exception as exc:
        problems.append(f"mcp configure/tool failed: {exc}")
        ns = []

    if problems:
        typer.echo("FAIL: " + "; ".join(problems))
        raise typer.Exit(code=1)

    typer.echo(
        f"all checks passed: {len(store.node_ids())} nodes, "
        f"{len(store.all_edges())} edges, namespaces={ns}"
    )


if __name__ == "__main__":
    app()
