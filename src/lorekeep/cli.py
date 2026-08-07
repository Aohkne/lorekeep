"""Lorekeep CLI."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import typer

from lorekeep import __version__
from lorekeep.compile.providers import LiteLLMProvider
from lorekeep.config import Config, load_config
from lorekeep.models import now_iso
from lorekeep.pipeline import compile_graph
from lorekeep.paths import resolve_paths
from lorekeep.defaults import DEFAULT_CONFIG_YAML, DEFAULT_SCHEMA
from lorekeep.providers import NATIVE_PROVIDERS, model_provider, validate_model_prefix
from lorekeep.schema_io import load_schema

log = logging.getLogger("lorekeep")

app = typer.Typer(help="Lorekeep — compile team docs into a temporal knowledge graph.")


# Empty callback forces multi-command mode so subcommands are not auto-promoted.
@app.callback()
def _main(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug-level logs."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Warnings only; suppress progress."),
) -> None:
    """Lorekeep — compile team docs into a temporal knowledge graph."""
    import logging as _logging
    from lorekeep.output import configure_logging
    if os.environ.get("LOREKEEP_DEBUG"):
        verbose = True
    level = _logging.DEBUG if verbose else (_logging.WARNING if quiet else _logging.INFO)
    configure_logging(level)


# Agent subcommand group (created early so commands below can register on it).
agent_app = typer.Typer(
    help="Agent operations: ingest, lint, suggest, status, watch, profile, "
         "contribution, service.",
)
app.add_typer(agent_app, name="agent")


def _build_provider(config: Config) -> LiteLLMProvider:
    """Create a real LLM provider from config.  Shared by compile + import."""
    from lorekeep.compile.providers import setup_observability

    obs = config.observability
    if obs.provider:
        setup_observability(
            provider=obs.provider,
            api_key_env=obs.api_key_env,
            project=obs.project,
            api_url=obs.api_url,
        )

    api_key = None
    if config.provider.api_key_env:
        api_key = os.environ.get(config.provider.api_key_env)
    if not api_key:
        api_key = config.provider.api_key
    validate_model_prefix(config.provider.model)  # defense-in-depth (load_config already gates)
    return LiteLLMProvider(
        model=config.provider.model,
        api_base=config.provider.api_base,
        temperature=config.provider.temperature,
        api_key=api_key,
        timeout_seconds=config.provider.timeout_seconds,
        max_retries=config.provider.max_retries,
    )


def _make_provider(config: Config) -> LiteLLMProvider:
    """Create provider for compile.  Tests monkeypatch this to inject FakeProvider."""
    return _build_provider(config)


def _make_import_provider(config: Config) -> LiteLLMProvider:
    """Create provider for import deep mode.  Tests monkeypatch this."""
    return _build_provider(config)


def _has_provider(config: Config) -> bool:
    """Check if a provider API key is available.  Tests monkeypatch this."""
    return bool(
        (config.provider.api_key_env and os.environ.get(config.provider.api_key_env))
        or config.provider.api_key
    )


@app.command()
def version() -> None:
    """Print the Lorekeep version."""
    typer.echo(f"lorekeep {__version__}")


@app.command(hidden=True)
def hook() -> None:
    """Session lifecycle hook: quick-import memories from every agent.

    Registry-driven, so an agent starts contributing the moment it declares
    an ingest path. Idempotent via SHA-256 manifest — a hook that fires on
    every turn costs nothing when nothing changed.
    """
    from lorekeep.integrations.registry import all_specs

    p = resolve_paths()
    total = 0

    for spec in all_specs():
        if spec.memory is None or spec.memory_ns is None:
            continue
        try:
            written = getattr(spec.importer(), spec.memory.import_fn)(
                p["raw"], namespace=spec.memory_ns,
            )
            total += len(written)
        except Exception as exc:
            log.warning(
                "hook memory import failed agent=%s error_type=%s",
                spec.name, type(exc).__name__,
                extra={"event": "hook.memory_import_failed"},
            )

    if total:
        log.info(
            "memory hook completed file_count=%s", total,
            extra={"event": "hook.complete"},
        )
        typer.echo(f"lorekeep: imported {total} memory file(s)")


def _report_compile_errors(manifest, *, exit_on_total_failure: bool = True) -> None:
    """Surface compile errors from a :class:`~lorekeep.models.Manifest`.

    ``compile_graph`` uses a skip-and-log strategy: per-chunk failures are
    collected in ``manifest.errors`` rather than raised.  Without this helper
    the user would see ``compiled: 0 nodes`` and an exit code of 0, with no
    indication that every LLM extraction call failed (e.g. wrong model string,
    missing API key, bad ``api_base``).

    * **Partial failure** — some chunks failed but nodes were produced.
      Prints a one-line summary to stderr so the user knows to check
      ``manifest.json`` for details.
    * **Total failure** — ``node_count == 0`` with ``chunk_count > 0``.
      Prints every error to stderr and, when *exit_on_total_failure* is
      ``True`` (the interactive ``compile`` command), exits with code 1.
      The daemon passes ``False`` so it can keep running.
    """
    errs = manifest.errors or []
    if not errs:
        return
    for e in errs:
        log.error(
            "compile error line=%s", e.line,
            extra={"event": "compile.manifest_error"},
        )
    from lorekeep.output import dim, error, warn
    total_fail = manifest.node_count == 0 and manifest.chunk_count > 0
    if total_fail:
        error(
            f"compile: ALL {manifest.chunk_count} chunk(s) failed — 0 nodes produced. "
            "Check provider config (model, api_base, api_key)."
        )
        for e in errs:
            dim(f"  {e.path}:{e.line}: {e.message}")
        if exit_on_total_failure:
            raise typer.Exit(code=1)
    else:
        # Systemic-error heuristic: if most chunks failed with the SAME message,
        # it's almost always a provider config issue (bad model/api_base/api_key)
        # rather than per-doc content. Surface every identical error + a hint so
        # the user isn't left with a one-line summary and an empty-looking graph.
        messages = [e.message for e in errs]
        distinct = set(messages)
        systemic = len(errs) >= 3 and (len(distinct) == 1 or max(messages.count(m) for m in distinct) >= 0.8 * len(errs))
        if systemic:
            error(
                f"compile: {len(errs)} of {manifest.chunk_count} chunk(s) failed "
                f"with the same error ({manifest.node_count} nodes still produced)."
            )
            for e in errs:
                dim(f"  {e.path}:{e.line}: {e.message}")
            dim(
                "  hint: identical errors across chunks usually mean a provider "
                "config issue (model/api_base/api_key). Run 'lorekeep doctor'."
            )
        else:
            warn(
                f"compile: {len(errs)} chunk(s) failed (partial — "
                f"{manifest.node_count} nodes still produced). See manifest.json."
            )


def _report_content_quality(manifest) -> None:
    """Warn about readability gaps without rejecting otherwise valid facts."""
    quality = manifest.content_quality
    if quality is None:
        return
    issues: list[str] = []
    if quality.node_summary_coverage < 1.0:
        issues.append(f"summaries {quality.node_summary_coverage:.0%}")
    if quality.edge_description_coverage < 1.0:
        issues.append(
            f"relationship explanations {quality.edge_description_coverage:.0%}"
        )
    if quality.generic_edge_ratio > 0.5:
        issues.append(f"generic edges {quality.generic_edge_ratio:.0%}")
    if quality.duplicate_label_count:
        issues.append(f"duplicate labels {quality.duplicate_label_count}")
    if issues:
        from lorekeep.output import warn
        warn(
            "compile: content quality needs attention ("
            + ", ".join(issues)
            + "). Facts were kept; see manifest.json and wiki/overview.md."
        )


def _progress_ctx(raw_root, chunk_lines):
    """Context manager for a compile progress bar.

    tty + not quiet → a Rich Progress bar (total pre-counted via ingest, a pure
    file-slicer). Else → a nullcontext whose handle is None, so compile_graph
    runs silent (current behavior under CliRunner / the daemon's agent.log).
    """
    from contextlib import nullcontext
    from lorekeep.compile.ingest import ingest as _ingest
    from lorekeep.output import is_quiet, is_terminal, progress
    if not is_quiet() and is_terminal():
        total = len(_ingest(raw_root, chunk_lines=chunk_lines))
        return progress(f"Compiling {total} chunk(s)", total=total)
    return nullcontext(None)


def _progress_cb(handle):
    """Build an on_progress callback from a progress handle (None → None)."""
    if not handle:
        return None
    return lambda i, total, chunk: handle.advance()


@app.command()
def compile() -> None:
    """Compile raw/ → facts.jsonl + merge pending + generate wiki (all-in-one)."""
    from lorekeep.output import ok
    p = resolve_paths()
    schema = load_schema(p["schema"])
    config = load_config(p["config"])
    provider = _make_provider(config)

    with _progress_ctx(p["raw"], config.compile.chunk_lines) as handle:
        manifest = compile_graph(
            raw_root=p["raw"], out_dir=p["out"], schema=schema,
            provider=provider, cache_path=p["cache"], chunk_lines=config.compile.chunk_lines,
            on_progress=_progress_cb(handle),
            personal_ns=config.ns.personal_namespace,
        )

    ok(f"compiled: {manifest.node_count} nodes, {manifest.edge_count} edges, "
       f"run_id={manifest.run_id}, facts_hash={manifest.facts_hash}")

    _report_compile_errors(manifest)
    _report_content_quality(manifest)

    pending_dir = p.get("pending")
    resolved = False
    if pending_dir and pending_dir.exists():
        resolved = _do_auto_resolve(
            p["out"], pending_dir, p.get("wiki"), p.get("schema"),
            replay_accepted=True,
        )

    if not resolved:
        _auto_generate_wiki(p["out"], p["wiki"], p.get("schema"))


def _open_in_obsidian(path: Path) -> None:
    """Open *path* as an Obsidian vault via the ``obsidian://`` URL scheme.

    Non-fatal: if Obsidian (or the platform opener) is missing, warn with the
    raw path so the user can open it manually. The wiki is already generated.
    """
    import subprocess
    import sys
    import urllib.parse
    from lorekeep.output import warn
    url = "obsidian://open?path=" + urllib.parse.quote(str(path.resolve()), safe="")
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", url], check=False)
        elif sys.platform.startswith("win"):
            subprocess.run(["cmd", "/c", "start", "", url], check=False)
        else:
            subprocess.run(["xdg-open", url], check=False)
    except (FileNotFoundError, OSError):
        warn(f"could not launch Obsidian; open this folder as a vault manually: {path}")


@app.command()
def wiki(
    open: bool = typer.Option(False, "--open", help="Open the wiki in Obsidian after generating."),
) -> None:
    """Generate Obsidian-compatible wiki from facts.jsonl."""
    from lorekeep.output import error, ok
    from lorekeep.wiki import generate_wiki
    p = resolve_paths()
    schema = load_schema(p["schema"]) if p["schema"].exists() else None
    result = generate_wiki(p["out"], p["wiki"], schema=schema)
    if "error" in result:
        error(f"wiki: {result['error']}")
        raise typer.Exit(code=1)
    ok(f"wiki: {result['pages']} pages written to {p['wiki']}")
    if open:
        _open_in_obsidian(p["wiki"])


@agent_app.command()
def profile(
    open: bool = typer.Option(False, "--open", help="Open your raw profile dir in Obsidian/Tolaria."),
) -> None:
    """Show / open your personal profile source (raw/<ns>/).

    The wiki is a derived view; the editable source is raw/<ns>/about.md +
    profile.md. Edit those (in Obsidian/Tolaria), then `lorekeep compile`.
    """
    from lorekeep.output import info
    p = resolve_paths()
    try:
        ns = load_config(p["config"]).ns.personal_namespace
    except Exception:
        ns = "me"
    ns_dir = p["raw"] / ns
    info(f"profile source: {ns_dir}")
    info("edit about.md / profile.md here, then `lorekeep compile` — the wiki reflects you")
    if open:
        _open_in_obsidian(ns_dir)


@agent_app.command()
def contribution() -> None:
    """Suggest team-knowledge gaps: nodes in your personal namespace not yet shared.

    Scans the compiled graph for nodes of shareable types (service, project,
    decision, domain, skill) that live only in your personal namespace — i.e.
    things you know but the team graph doesn't. Move the source doc to a team
    namespace (raw/<team>/) and re-compile to share. Read-only.
    """
    from collections import defaultdict
    from lorekeep.compile.resolve import _normalize_id
    from lorekeep.output import dim, info, ok, warn
    from lorekeep.store.graph import GraphStore
    p = resolve_paths()
    facts = p["out"] / "facts.jsonl"
    if not facts.exists():
        warn(f"no compiled graph at {facts} — run `lorekeep compile` first")
        raise typer.Exit(code=1)
    try:
        personal_ns = load_config(p["config"]).ns.personal_namespace
    except Exception:
        personal_ns = "me"
    SHARE_TYPES = {"service", "project", "decision", "domain", "skill"}

    store = GraphStore.from_jsonl(facts)
    where: dict[str, set[str]] = defaultdict(set)
    for n in store.all_nodes():
        where[_normalize_id(n.id)].update(n.ns)

    gaps = [
        n for n in store.all_nodes()
        if personal_ns in n.ns
        and n.type in SHARE_TYPES
        and not (where[_normalize_id(n.id)] - {personal_ns, "public"})
    ]
    gaps.sort(key=lambda n: (n.type, n.id))

    if not gaps:
        ok(f"no contribution gaps — your '{personal_ns}' knowledge is already shared")
        return
    info(f"{len(gaps)} node(s) in '{personal_ns}' not in any team namespace:")
    for n in gaps:
        dim(f"  {n.id} ({n.type}) — consider moving its source doc to raw/<team>/")





@app.command(name="eval", hidden=True)
def eval_cmd() -> None:
    """Run Tier-1 construction-quality evaluation vs the gold corpus."""
    p = resolve_paths()
    gold_dir = Path(os.environ.get("LOREKEEP_GOLD", "tests/fixtures/gold"))
    from lorekeep.eval.construction import extraction_report, structure_report
    report = {
        "extraction": extraction_report(p["out"], gold_dir),
        "structure": structure_report(p["out"]),
    }
    results_path = Path(os.environ.get("LOREKEEP_EVAL_RESULTS",
                                       ".lorekeep/eval/results.json"))
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(json.dumps(report, indent=2, sort_keys=True))
    typer.echo(json.dumps(report, indent=2, sort_keys=True))


@app.command(name="eval-locomo", hidden=True)
def eval_locomo_cmd(
    data: str = typer.Option("", "--data", help="Path to locomo10.json"),
    compile_first: bool = typer.Option(
        False, "--compile",
        help="Convert JSON to raw/ + compile before running eval",
    ),
) -> None:
    """Run Tier-2 LoCoMo retrieval eval."""
    from lorekeep.eval.locomo import convert_locomo, locomo_report

    p = resolve_paths()
    data_path = Path(data) if data else Path(
        os.environ.get("LOREKEEP_LOCOMO", "locomo10.json")
    )

    if compile_first:
        if not data_path.exists():
            typer.echo(f"eval-locomo: data file not found: {data_path}")
            raise typer.Exit(code=1)
        count = convert_locomo(data_path, p["raw"] / "locomo")
        typer.echo(f"eval-locomo: converted {count} session files to {p['raw'] / 'locomo'}")
        schema = load_schema(p["schema"])
        config = load_config(p["config"])
        provider = _make_provider(config)
        manifest = compile_graph(
            raw_root=p["raw"], out_dir=p["out"], schema=schema,
            provider=provider, cache_path=p["cache"],
            chunk_lines=config.compile.chunk_lines,
            personal_ns=config.ns.personal_namespace,
        )
        typer.echo(f"eval-locomo: compiled {manifest.node_count} nodes, {manifest.edge_count} edges")

    raw_ns = os.environ.get("LOREKEEP_NS")
    allowed = [x.strip() for x in raw_ns.split(",")] if raw_ns else ["locomo"]
    report = locomo_report(p["out"], data_path, allowed, raw_dir=p["raw"])
    if "error" in report:
        typer.echo(f"eval-locomo: {report['error']}")
        raise typer.Exit(code=1)

    results_path = Path(os.environ.get(
        "LOREKEEP_EVAL_RESULTS", ".lorekeep/eval/locomo-results.json"
    ))
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(json.dumps(report, indent=2, sort_keys=True))

    s = report["summary"]
    typer.echo(f"\nLoCoMo Tier-2 Eval ({s['total_questions']} questions)")
    typer.echo(f"Overall F1: {s['overall_f1']}")
    typer.echo("")
    typer.echo(f"{'Category':<20} {'Count':>6} {'F1':>8}")
    typer.echo("-" * 36)
    for cat, stats in s["per_category"].items():
        typer.echo(f"{cat:<20} {stats['count']:>6} {stats['f1']:>8.4f}")


def _with_resolve_lock(func):
    """Typer-safe decorator serializing manual resolve with daemon resolve."""
    from functools import wraps

    @wraps(func)
    def wrapped(*args, **kwargs):
        from lorekeep.journal import resolve_lock
        pending = resolve_paths().get("pending")
        if pending is None:
            return func(*args, **kwargs)
        with resolve_lock(pending):
            return func(*args, **kwargs)

    return wrapped


@app.command()
@_with_resolve_lock
def resolve(
    archive: bool = typer.Option(
        False, "--archive",
        help="Archive processed journal entries instead of truncating",
    ),
) -> None:
    """Merge pending journal entries into facts.jsonl (full resolve pass)."""
    p = resolve_paths()
    from lorekeep.store.graph import GraphStore
    from lorekeep.facts_io import read_facts
    from lorekeep.compile.resolve import resolve as resolve_facts, merge_journals
    from lorekeep.compile.writer import write_graph
    from lorekeep.journal import load_journals, update_journal_status
    from lorekeep.models import Manifest
    from lorekeep.pipeline import measure_content_quality

    facts_path = p["out"] / "facts.jsonl"
    pending = p.get("pending")
    if not pending or not pending.exists():
        typer.echo("resolve: no pending directory, nothing to do")
        return

    journals = load_journals(pending)
    pending_entries = [j for j in journals if j.status == "pending"]
    if not pending_entries:
        typer.echo("resolve: no pending journal entries")
        return

    # Load current facts
    existing_nodes = []
    existing_edges = []
    if facts_path.exists():
        facts = read_facts(facts_path)
        from lorekeep.models import Edge, Node
        for f in facts:
            if isinstance(f, Node):
                existing_nodes.append(f)
            else:
                existing_edges.append(f)

    schema = load_schema(p["schema"])
    # Merge journals
    merged = merge_journals(
        existing_nodes, existing_edges, pending_entries, schema=schema,
    )

    # Run standard resolve over merged facts
    resolved = resolve_facts(
        merged.nodes, merged.edges, schema=schema,
    )

    # Build manifest
    manifest = Manifest(
        schema_version=schema.version,
        chunk_count=0,
        node_count=len(resolved.nodes),
        edge_count=len(resolved.edges),
        run_id="resolve",
        facts_hash="",
        compiled_at=now_iso(),
        merged_count=merged.merge_count,
        quarantined_count=merged.quarantine_count,
        flagged_count=merged.flagged_count,
        quarantine=[{"fact": q[0].fact, "reason": q[1]} for q in resolved.quarantined],
        review=[{"fact_id": f[0].fact.get("id", ""), "reason": f[1]}
                for f in merged.flagged],
        content_quality=measure_content_quality(
            resolved.nodes, resolved.edges, schema,
        ),
    )
    write_graph(p["out"], resolved.nodes, resolved.edges, manifest)

    # Update journal status per namespace
    ns_to_merged: dict[str, set[str]] = {}
    ns_to_flagged: dict[str, set[str]] = {}
    ns_to_quarantined: dict[str, set[str]] = {}
    for entry, _ in merged.merged:
        ns_to_merged.setdefault(entry.ns, set()).add(
            entry.entry_id or entry.proposed_at
        )
    for entry, _ in merged.flagged:
        ns_to_flagged.setdefault(entry.ns, set()).add(
            entry.entry_id or entry.proposed_at
        )
    for entry, _ in merged.quarantined:
        ns_to_quarantined.setdefault(entry.ns, set()).add(
            entry.entry_id or entry.proposed_at
        )

    # Flagged entries are still merged into the graph (just flagged for review)
    for ns, timestamps in ns_to_flagged.items():
        existing = ns_to_merged.get(ns, set())
        ns_to_merged[ns] = existing | timestamps

    for ns, timestamps in ns_to_merged.items():
        update_journal_status(pending, ns, timestamps, "merged")
    for ns, timestamps in ns_to_quarantined.items():
        # Don't overwrite merged status for entries already handled
        already = ns_to_merged.get(ns, set())
        to_quarantine = timestamps - already
        if to_quarantine:
            update_journal_status(pending, ns, to_quarantine, "quarantined")

    typer.echo(
        f"resolve: {len(resolved.nodes)} nodes, {len(resolved.edges)} edges — "
        f"{merged.merge_count} merged, {merged.flagged_count} flagged, "
        f"{merged.quarantine_count} quarantined"
    )

    if merged.merge_count > 0 or merged.flagged_count > 0:
        _auto_generate_wiki(p["out"], p["wiki"], p.get("schema"))


@app.command()
def serve(
    transport: str = typer.Option("stdio", "--transport", help="stdio (default) | http"),
) -> None:
    """Serve the scoped graph over MCP."""
    p = resolve_paths()
    raw_ns = os.environ.get("LOREKEEP_NS")
    if raw_ns:
        allowed = [x.strip() for x in raw_ns.split(",") if x.strip()]
    else:
        allowed = load_config(p["config"]).ns.default
    try:
        from lorekeep.mcp_server import configure, mcp
    except ImportError as exc:
        from lorekeep.output import error
        missing = str(exc)
        if "fastmcp" in missing.lower():
            error("lorekeep requires mcp v1.x, but mcp v2.x is installed (FastMCP was removed).")
            error("Fix: pip install 'mcp>=1.0,<2.0'  (or: uv pip install 'mcp>=1.0,<2.0')")
        else:
            error(f"'lorekeep serve' requires the 'mcp' package, which is not installed: {exc}")
            error("Fix: pip install mcp  (or: uv pip install mcp)")
        log.error(
            "serve: mcp dependency missing error_type=ImportError detail=%s",
            "fastmcp" if "fastmcp" in missing.lower() else "mcp",
            extra={"event": "serve.mcp_missing"},
        )
        raise typer.Exit(code=1)
    try:
        configure(graph_dir=p["out"], allowed_ns=allowed, schema_path=p["schema"], pending_dir=p.get("pending"))
    except FileNotFoundError as exc:
        from lorekeep.output import error
        error(str(exc))
        log.error(
            "serve: graph not built detail=%s", exc,
            extra={"event": "serve.no_graph"},
        )
        raise typer.Exit(code=1)
    log.info(
        "MCP server starting transport=%s namespace_count=%s", transport, len(allowed),
        extra={"event": "mcp.start"},
    )
    try:
        mcp.run(transport=transport)
    except Exception as exc:
        log.exception(
            "MCP server stopped unexpectedly error_type=%s", type(exc).__name__,
            extra={"event": "mcp.failed"},
        )
        raise


mcp_app = typer.Typer(help="Coding-agent integration.")
app.add_typer(mcp_app, name="mcp")

config_app = typer.Typer(help="View and edit lorekeep config.")
app.add_typer(config_app, name="config")
schema_app = typer.Typer(help="Inspect and upgrade the graph schema.")
app.add_typer(schema_app, name="schema")
support_app = typer.Typer(
    help="Diagnostics and automatic error reporting.",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(support_app, name="support")


@support_app.callback()
def support(
    ctx: typer.Context,
    output: Path | None = typer.Option(None, "--output", "-o", help="Write the ZIP to this path."),
    report_only: bool = typer.Option(False, "--report-only", help="Print the report without creating a ZIP."),
    no_print: bool = typer.Option(False, "--no-print", help="Create the ZIP without printing the report."),
) -> None:
    """Print a support report and create its redacted attachment bundle."""
    if ctx.invoked_subcommand is not None:
        return
    if report_only and no_print:
        raise typer.BadParameter("--report-only and --no-print cannot be used together")
    if report_only and output is not None:
        raise typer.BadParameter("--output is only used when creating a bundle")

    from lorekeep.support import build_report, create_bundle
    if not no_print:
        typer.echo(build_report(), nl=False)
    if report_only:
        return
    path, digest = create_bundle(output)
    if not no_print:
        typer.echo()
    typer.echo(f"support bundle: {path}")
    typer.echo(f"sha256: {digest}")


@support_app.command("report", hidden=True)
def support_report(
    output: Path | None = typer.Option(None, "--output", "-o", help="Write Markdown to this path."),
) -> None:
    """Print a metadata-only report suitable for a GitHub issue."""
    from lorekeep.support import build_report, write_report
    if output is None:
        typer.echo(build_report(), nl=False)
    else:
        write_report(output)
        typer.echo(f"support report: {output}")


@support_app.command("bundle", hidden=True)
def support_bundle(
    output: Path | None = typer.Option(None, "--output", "-o", help="Write the ZIP to this path."),
) -> None:
    """Create a redacted, allowlisted ZIP for attachment to a bug report."""
    from lorekeep.support import create_bundle
    path, digest = create_bundle(output)
    typer.echo(f"support bundle: {path}")
    typer.echo(f"sha256: {digest}")


# ── support auto-reporting (merged from bugreport) ───────────────────────────

def _set_bugreport_enabled(value: bool) -> None:
    """Write bugreport.enabled in config.yaml."""
    import yaml
    from lorekeep.output import ok
    p = resolve_paths()
    if not p["config"].exists():
        typer.echo("No config.yaml found — run `lorekeep init` first.")
        raise typer.Exit(code=1)
    data = yaml.safe_load(p["config"].read_text(encoding="utf-8")) or {}
    br = data.setdefault("bugreport", {})
    br["enabled"] = value
    p["config"].write_text(
        yaml.dump(data, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    ok(f"auto bug-report {'enabled' if value else 'disabled'}")


@support_app.command("on")
def support_on() -> None:
    """Enable automatic GitHub issue creation on errors."""
    _set_bugreport_enabled(True)


@support_app.command("off")
def support_off() -> None:
    """Disable automatic GitHub issue creation on errors."""
    _set_bugreport_enabled(False)


@support_app.command("status")
def support_status() -> None:
    """Show auto-report configuration, dedup stats, and token resolution."""
    import json
    from lorekeep.bugreport import _dedup_path, _load_dedup, _resolve_token
    from lorekeep.config import load_config
    from lorekeep.output import dim, info

    p = resolve_paths()
    cfg = load_config(p["config"])
    br = cfg.bugreport

    state = "enabled" if br.enabled else "disabled"
    info(f"auto bug-report: {state}")
    typer.echo(f"  repo: {br.repo}")
    typer.echo(f"  token env: {br.token_env}")

    # Show token resolution from all sources.
    token = _resolve_token(br.token_env)
    if token:
        sources = []
        if os.environ.get(br.token_env):
            sources.append(br.token_env)
        if os.environ.get("GITHUB_TOKEN"):
            sources.append("GITHUB_TOKEN")
        typer.echo(f"  token source: {', '.join(sources) or 'gh auth'}")
    else:
        typer.echo("  token: not found")

    typer.echo(f"  labels: {', '.join(br.labels)}")

    dpath = _dedup_path()
    dedup = _load_dedup(dpath)
    reported = len(dedup)
    total = sum(v.get("count", 1) for v in dedup.values())
    typer.echo(f"  dedup file: {dpath}")
    typer.echo(f"  issues created: {reported}")
    typer.echo(f"  total occurrences: {total}")
    if not dedup:
        dim("  (no errors reported yet)")


@schema_app.command("upgrade")
def schema_upgrade(
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the upgrade without writing."),
    force: bool = typer.Option(False, "--force", help="Replace a custom older schema after backing it up."),
) -> None:
    """Upgrade a stock ontology schema to the latest version, preserving a backup."""
    from lorekeep.output import info, ok, warn
    from lorekeep.schema_io import upgrade_schema

    p = resolve_paths()
    result = upgrade_schema(p["schema"], dry_run=dry_run, force=force)
    if result["custom"] and not result["changed"]:
        warn(
            "custom schema detected; re-run with --force only after reviewing "
            "the current ontology changes"
        )
        raise typer.Exit(code=2)
    if not result["changed"]:
        ok(f"schema already at version {result['to']}")
        return
    action = "would upgrade" if dry_run else "upgraded"
    info(f"{action} schema v{result['from']} → v{result['to']}")
    if not dry_run:
        ok(f"backup: {result['backup']}")
        info("next: run `lorekeep compile` to rebuild the derived graph")


@config_app.command("show")
def config_show() -> None:
    """Print the current config.yaml."""
    p = resolve_paths()
    if not p["config"].exists():
        typer.echo("No config.yaml found — run `lorekeep init` first.")
        raise typer.Exit(code=1)
    typer.echo(p["config"].read_text(encoding="utf-8"))


@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help="Dot-notation key (e.g. provider.model)"),
    value: str = typer.Argument(..., help="Value to set"),
) -> None:
    """Set a config value (e.g. `config set provider.model deepseek/deepseek-chat`)."""
    import yaml
    p = resolve_paths()
    if not p["config"].exists():
        typer.echo("No config.yaml found — run `lorekeep init` first.")
        raise typer.Exit(code=1)

    data = yaml.safe_load(p["config"].read_text(encoding="utf-8")) or {}

    keys = key.split(".")
    target = data
    for k in keys[:-1]:
        target = target.setdefault(k, {})

    final_key = keys[-1]
    if isinstance(target.get(final_key), list):
        target[final_key] = [v.strip() for v in value.split(",")]
    elif isinstance(target.get(final_key), bool):
        target[final_key] = value.lower() in ("true", "1", "yes")
    elif isinstance(target.get(final_key), int):
        target[final_key] = int(value)
    elif isinstance(target.get(final_key), float):
        target[final_key] = float(value)
    elif value.lower() in ("null", "none", ""):
        target[final_key] = None
    else:
        target[final_key] = value

    if key == "provider.model":
        try:
            validate_model_prefix(target[final_key])
        except ValueError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1)

    p["config"].write_text(
        yaml.dump(data, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    typer.echo(f"  {key} = {value}")


@mcp_app.command("add")
def mcp_add(
    agent: str = typer.Option(..., "--agent", help="claude | cursor | codex | opencode"),
    scope: str = typer.Option("project", "--scope", help="project | user"),
    ns: str = typer.Option(None, "--ns", help="namespace to scope the agent to"),
) -> None:
    """Write the agent's MCP config + print an agent-memory snippet."""
    from lorekeep.integrations.common import agent_memory_snippet, resolve_command

    p = resolve_paths()
    config = load_config(p["config"])
    command, args = resolve_command(config.install_source)
    hook_cmd, hook_args = resolve_command(config.install_source, ["hook"])

    if scope not in ("project", "user"):
        typer.echo(f"unknown scope: {scope} (choose project|user)")
        raise typer.Exit(code=1)
    writers = _agent_writers()
    if agent not in writers:
        typer.echo(f"unknown agent: {agent} (choose claude|cursor|codex|opencode)")
        raise typer.Exit(code=1)

    writer = writers[agent]
    target = Path.cwd()
    written = writer.write_config(target, command, args, ns, scope=scope)
    if written is None:
        typer.echo(f"{agent} config unchanged -> {writer.config_target(target, scope)}")
    else:
        typer.echo(f"wrote {agent} config -> {written}")
    if hasattr(writer, "write_hook"):
        hook_path = writer.write_hook(target, hook_cmd, hook_args, scope=scope)
        if hook_path is None:
            typer.echo(f"session-end hook unchanged -> {writer.hook_target(target, scope)}")
        else:
            typer.echo(f"wrote session-end hook -> {hook_path}")
    typer.echo("\n" + agent_memory_snippet())


@app.command()
def doctor() -> None:
    """Validate the full install: graph loads with no dangling edges, schema
    is valid, MCP tools respond, and the configured LLM provider is reachable.

    This is the sole validation command — run it after `compile` or when
    troubleshooting. Provider ping is skipped automatically when no API key
    is configured."""
    p = resolve_paths()
    problems = []
    notes = []
    from lorekeep.output import error as _err, info as _info, ok as _ok

    facts_path = p["out"] / "facts.jsonl"
    if not facts_path.exists():
        _err(f"FAIL: facts.jsonl not found at {facts_path}")
        raise typer.Exit(code=1)

    try:
        from lorekeep.store.graph import GraphStore
        store = GraphStore.from_jsonl(facts_path)
    except Exception as exc:
        _err(f"FAIL: cannot load graph: {exc}")
        raise typer.Exit(code=1)

    if not p["schema"].exists():
        problems.append("schema.json missing")
    else:
        try:
            load_schema(p["schema"])
        except Exception as exc:
            problems.append(f"schema invalid: {exc}")

    # Load config once; a bare model fails fast here (reported, not crashed).
    try:
        config = load_config(p["config"])
    except ValueError as exc:
        _err(f"FAIL: provider config: {exc}")
        raise typer.Exit(code=1)

    raw_ns = os.environ.get("LOREKEEP_NS")
    allowed = [x.strip() for x in raw_ns.split(",")] if raw_ns else config.ns.default

    try:
        from lorekeep.mcp_server import configure, list_namespaces
        configure(graph_dir=p["out"], allowed_ns=allowed, schema_path=p["schema"], pending_dir=p.get("pending"))
        ns = list_namespaces()
    except Exception as exc:
        problems.append(f"mcp configure/tool failed: {exc}")
        ns = []

    # Hint: api_base is redundant for native providers — litellm already knows
    # their endpoint. Surfaced as a non-fatal note (a user may intentionally
    # point a native provider at a mirror/proxy).
    if config.provider.api_base:
        prefix = model_provider(config.provider.model)
        if prefix in NATIVE_PROVIDERS:
            notes.append(
                f"provider: hint — api_base set for {prefix}/, but litellm "
                "already knows this endpoint; usually unnecessary (only "
                "vllm/lm_studio/proxies/non-default-ollama need api_base)."
            )

    # Provider connectivity probe — catches the most common breakage (bad
    # model/api_base/api_key) that a graph/schema check alone misses.
    if os.environ.get("LOREKEEP_DOCTOR_NO_PING") == "1":
        notes.append("provider: ping skipped (LOREKEEP_DOCTOR_NO_PING=1)")
    elif not _has_provider(config):
        notes.append("provider: skipped (no API key set — compile will skip until you add one)")
    else:
        try:
            _make_provider(config).ping()
            notes.append(f"provider: ok ({config.provider.model})")
        except Exception as exc:
            msg = str(exc).lower()
            if "401" in msg or "authentication" in msg or "unauthorized" in msg:
                problems.append("provider: AUTH FAILED (bad API key)")
            elif "404" in msg or "not found" in msg or "model" in msg and "exist" in msg:
                problems.append(f"provider: MODEL NOT FOUND ({config.provider.model}) — check the model string")
            elif "connection" in msg or "timeout" in msg or "unreachable" in msg or "refused" in msg:
                problems.append("provider: ENDPOINT UNREACHABLE (check api_base / network)")
            else:
                problems.append(f"provider: FAILED — {exc}")

    if problems:
        _err("FAIL: " + "; ".join(problems))
        raise typer.Exit(code=1)

    _ok(
        f"all checks passed: {len(store.node_ids())} nodes, "
        f"{len(store.all_edges())} edges, namespaces={ns}"
    )
    for note in notes:
        _info(note)


def _is_interactive() -> bool:
    """True if stdin is a TTY (user can answer prompts)."""
    import sys
    return sys.stdin.isatty()


@app.command()
def init(
    yes: bool = typer.Option(
        False, "--yes", "-y",
        help="Skip interactive prompts, use defaults",
    ),
    watch: bool = typer.Option(
        True, "--watch/--no-watch",
        help="Start the daemon (agent watch) in background after setup",
    ),
) -> None:
    """Bootstrap the data home, wire agents, import sessions, compile, and start daemon."""
    p = resolve_paths()
    created = []
    p["config"].parent.mkdir(parents=True, exist_ok=True)
    config_existed = p["config"].exists()
    ns = "public"
    name = ""
    bio = ""

    if not config_existed:
        if not yes and _is_interactive():
            ns, name, bio = _interactive_init(p)
        else:
            p["config"].write_text(DEFAULT_CONFIG_YAML)
            ns = load_config(p["config"]).ns.personal_namespace
        created.append(str(p["config"]))
    elif p["config"].exists():
        try:
            ns = load_config(p["config"]).ns.default[0]
        except Exception as exc:
            log.warning(
                "existing config could not be loaded error_type=%s",
                type(exc).__name__, extra={"event": "init.config_invalid"},
            )

    p["schema"].parent.mkdir(parents=True, exist_ok=True)
    if not p["schema"].exists():
        p["schema"].write_text(json.dumps(DEFAULT_SCHEMA, indent=2))
        created.append(str(p["schema"]))

    p["raw"].mkdir(parents=True, exist_ok=True)
    p["out"].mkdir(parents=True, exist_ok=True)
    p["pending"].mkdir(parents=True, exist_ok=True)

    from lorekeep.output import info, ok
    ok(f"home ready: config={p['config']}")
    info(f"  schema={p['schema']}  raw={p['raw']}  graph={p['out']}")
    if created:
        typer.echo(f"  wrote defaults: {created}")
    else:
        typer.echo("  (existing config/schema preserved)")

    # First file: the user's about.md (profile from onboarding).
    # Written on first init — always, even if raw/ has other files.
    if not config_existed:
        ns_dir = p["raw"] / ns
        ns_dir.mkdir(parents=True, exist_ok=True)
        about_path = ns_dir / "about.md"
        if not about_path.exists():
            about_md = (
                f"# {name or '(your name)'}\n\n"
                f"{bio or '(your bio — a one-line intro about you)'}\n"
            )
            about_path.write_text(about_md)
            typer.echo(f"  wrote: {about_path}")

            # Optional profile scaffold — the editable source for the personal
            # (subject-centric) namespace. User fills it via Obsidian/Tolaria;
            # the wiki is a derived view.
            profile_path = ns_dir / "profile.md"
            if not profile_path.exists():
                from lorekeep.defaults import DEFAULT_PROFILE_TEMPLATE
                profile_path.write_text(DEFAULT_PROFILE_TEMPLATE)
                typer.echo(f"  wrote: {profile_path}")
                typer.echo(
                    "  hint: edit profile.md (role/domains/skills/goals) in "
                    "Obsidian/Tolaria, then `lorekeep compile` — the wiki reflects you."
                )

    # --- One-click chain: wire → import → compile → daemon -----------------
    # Wiring runs on every init: it is free and idempotent, so re-running
    # init is how you pick up an agent installed after the first run.
    _auto_wire_agents(p, ns)

    if not config_existed:
        config = load_config(p["config"])
        if _has_provider(config):
            typer.echo("\n  Compiling your docs into the knowledge graph...")
        _auto_import_and_compile(p)

        # Show graph/wiki status after compile
        facts_path = p["out"] / "facts.jsonl"
        wiki_path = p["wiki"] / "index.md"
        if facts_path.exists():
            from lorekeep.store.graph import GraphStore
            store = GraphStore.from_jsonl(facts_path)
            typer.echo(
                f"  graph: {len(store.all_nodes())} nodes, {len(store.all_edges())} edges"
            )
            if wiki_path.exists():
                typer.echo(f"  wiki: {p['wiki']} (open in Obsidian to browse)")
        else:
            typer.echo(
                "  graph: empty — add docs under raw/ then run `lorekeep compile`"
            )

        typer.echo("\nRestart your agent → lorekeep tools are available.")

    # Daemon: start on fresh init or revive if dead (regardless of config_existed)
    if watch and _is_interactive():
        _start_daemon(p)
    elif watch and not _is_interactive():
        typer.echo("\n  (skipped daemon start in non-interactive mode — run `lorekeep agent watch` manually)")
    else:
        typer.echo(
            "\n  Daemon disabled (--no-watch). Agent-controlled mode:\n"
            "  - Run `lorekeep compile` after editing raw/*.md (does compile + resolve + wiki)\n"
            "  - Run `lorekeep resolve` to merge agent-proposed facts (zero LLM cost)\n"
            "  - MCP server lazy-reloads on next query — no daemon needed"
        )

    if config_existed:
        typer.echo("\nAlready initialized.")


def _interactive_init(p: dict) -> tuple[str, str, str]:
    """Walk the user through provider, model, API key, namespace, name, and bio.

    Returns ``(ns, name, bio)`` — the namespace plus the user's profile answers,
    so the caller can write the first file ``raw/<ns>/about.md``.
    """
    import yaml
    from lorekeep.providers import (
        list_models, search_providers,
        format_cost, is_dynamic, POPULAR,
    )

    typer.echo("\n=== Lorekeep setup ===\n")

    typer.echo(
        "Lorekeep uses an LLM at compile time to extract entities, relationships,\n"
        "and temporal facts from your markdown docs. It is NOT used at query time\n"
        "(agents read the graph directly via MCP — zero LLM cost per query).\n"
    )

    # ── Provider selection ─────────────────────────────────────────────
    typer.echo("Popular providers:")
    for i, prov in enumerate(POPULAR, 1):
        typer.echo(f"  {i}. {prov}")
    typer.echo(f"  {len(POPULAR) + 1}. [Search all providers]")
    typer.echo(f"  {len(POPULAR) + 2}. [Skip — configure later]")

    choice = typer.prompt("\nChoice", default="1")

    idx = int(choice) if choice.isdigit() else 0
    if idx == len(POPULAR) + 2 or choice.lower() == "skip":
        typer.echo("  → Skipped (edit config.yaml to add a provider later)\n")
        ns = typer.prompt("Default namespace", default="me")
        name = typer.prompt("Your name", default="")
        bio = typer.prompt("Bio (one-line intro)", default="")
        _write_config(p, model="openai/gpt-4o-mini",
                       api_base=None, api_key_env=None, api_key=None, ns=ns)
        return ns, name, bio

    if idx == len(POPULAR) + 1 or choice.lower() == "search":
        query = typer.prompt("Type provider name to search", default="")
        from lorekeep.providers import list_providers
        all_providers = list_providers()
        results = search_providers(query, all_providers)
        if not results:
            typer.echo("  → No matches. Using default (openai).")
            provider_name = "openai"
        else:
            typer.echo("")
            for i, (prov, count) in enumerate(results[:20], 1):
                typer.echo(f"  {i}. {prov} ({count} models)")
            sub = typer.prompt("Choice", default="1")
            sub_idx = int(sub) if sub.isdigit() else 1
            provider_name = results[min(sub_idx - 1, len(results) - 1)][0]
    elif 1 <= idx <= len(POPULAR):
        provider_name = POPULAR[idx - 1]
    else:
        provider_name = "openai"

    typer.echo(f"  → {provider_name}\n")

    # ── Model selection ────────────────────────────────────────────────
    typer.echo(f"Select a model for {provider_name} (used for knowledge extraction):\n")
    if is_dynamic(provider_name):
        model = typer.prompt(
            f"Model name (free-text for {provider_name})",
            default="llama3.2" if provider_name == "ollama" else "",
        )
        api_base = typer.prompt(
            "API base URL", default="http://localhost:11434" if provider_name == "ollama" else "",
        ) or None
    else:
        models = list_models(provider_name)
        if models:
            typer.echo("Models (chat, sorted by cost):")
            for i, m in enumerate(models[:20], 1):
                fc = format_cost(m.input_cost)
                fc_out = format_cost(m.output_cost)
                ctx = f"{m.max_input_tokens // 1000}K" if m.max_input_tokens else "?"
                typer.echo(f"  {i}. {m.model}  in={fc} out={fc_out} ctx={ctx}")
            typer.echo(f"  {len(models) + 1}. [Type custom model name]")
            mchoice = typer.prompt("Choice", default="1")
            midx = int(mchoice) if mchoice.isdigit() else 1
            if 1 <= midx <= len(models):
                model = models[midx - 1].model
            elif midx == len(models) + 1:
                model = typer.prompt("Model name (litellm string)", default="")
            else:
                model = models[0].model
        else:
            model = typer.prompt("Model name (litellm string)", default="")
        api_base = None

    # Prefix a bare model name with the explicitly-selected provider so the
    # written config is always a valid litellm string. (Not a guess — the user
    # picked this provider; only bare names get prefixed.)
    if "/" not in model:
        from lorekeep.providers import _normalize_model_name
        model = _normalize_model_name(model, provider_name)

    typer.echo(f"  → {model}\n")

    # ── API key (skip for local providers) ─────────────────────────────
    env_var = None
    if is_dynamic(provider_name):
        typer.echo("  → No API key needed for local provider.\n")
        api_key = None
    else:
        api_key = typer.prompt(
            "API key (saved into the gitignored config.yaml)",
            default="",
            hide_input=True,
        ) or None
        if api_key:
            typer.echo("  → key stored in config.yaml\n")
        else:
            env_var = typer.prompt(
                "API key env var name (or skip)",
                default=f"{provider_name.upper().replace('-', '_')}_API_KEY",
            )
            if env_var.lower() not in ("skip", ""):
                typer.echo(f"  → set {env_var} before compiling\n")
            else:
                env_var = None
                typer.echo("  → skipped (add key to config.yaml later)\n")

    # ── Namespace + profile ────────────────────────────────────────────
    ns = typer.prompt("Default namespace", default="me")
    name = typer.prompt("Your name", default="")
    typer.echo("  (your bio → raw/<ns>/about.md → compiled into the graph)")
    bio = typer.prompt("Bio (one-line intro)", default="")

    _write_config(
        p, model=model, api_base=api_base,
        api_key_env=env_var if not api_key else None,
        api_key=api_key, ns=ns,
    )
    return ns, name, bio


def _write_config(p, model, api_base, api_key_env, api_key, ns):
    """Write config.yaml from provider selection."""
    import yaml
    install_source = "local" if (Path.cwd() / ".lorekeep").exists() else "pypi"
    config = {
        "provider": {
            "model": model,
            "api_base": api_base,
            "api_key_env": api_key_env,
            "api_key": api_key,
            "temperature": 0.0,
            "timeout_seconds": 120,
            "max_retries": 2,
        },
        "compile": {"chunk_lines": 60},
        "ns": {"default": [ns], "personal": ns},
        "install_source": install_source,
    }
    p["config"].write_text(yaml.dump(config, default_flow_style=False, sort_keys=False))


def _wire_one(
    spec, target: Path, ns: str | None, *, scope: str = "project",
) -> tuple[Path | None, Path | None]:
    """Write one agent's MCP config + session-end hook.

    Returns ``(config_path, hook_path)``, each ``None`` when that file
    already held the desired wiring.
    """
    from lorekeep.integrations.common import resolve_command

    config = load_config(resolve_paths()["config"])
    command, args = resolve_command(config.install_source)
    hook_cmd, hook_args = resolve_command(config.install_source, ["hook"])

    writer = spec.writer()
    written = writer.write_config(target, command, args, ns, scope=scope)
    hooked = None
    if spec.supports_hook:
        hooked = writer.write_hook(target, hook_cmd, hook_args, scope=scope)
    return written, hooked


def _auto_wire_agents(p: dict, ns: str, *, scope: str = "project") -> None:
    """Detect every installed coding agent and write its MCP config.

    Idempotent: a writer that finds the desired entry already present
    reports ``unchanged`` instead of rewriting the file.
    """
    from lorekeep.integrations.detect import detect_agents
    from lorekeep.integrations.common import agent_memory_snippet
    from lorekeep.integrations.registry import find

    detected = detect_agents()
    if not detected:
        typer.echo("\n  No coding agents detected — run `lorekeep mcp add --agent <name>` after install.")
        return

    target = Path.cwd()
    typer.echo(f"\n  Detected agents: {', '.join(detected)}")
    for agent_name in detected:
        spec = find(agent_name)
        if spec is None:
            continue
        try:
            written, hooked = _wire_one(spec, target, ns, scope=scope)
            typer.echo(
                f"  wired {agent_name} -> {written}" if written
                else f"  {agent_name} already wired -> {spec.config_path(target, scope)}"
            )
            if spec.supports_hook and hooked:
                typer.echo(f"  hooked {agent_name} session-end -> {hooked}")
        except Exception as exc:
            log.warning(
                "agent wiring failed agent=%s error_type=%s",
                agent_name, type(exc).__name__,
                extra={"event": "init.agent_wiring_failed"},
            )
            typer.echo(f"  {agent_name}: failed ({exc})")

    typer.echo("\n  " + agent_memory_snippet().replace("\n", "\n  ").strip())


def _agent_writers() -> dict:
    """Return the agent-name → writer-module mapping (lazy import)."""
    from lorekeep.integrations.registry import all_specs
    return {spec.name: spec.writer() for spec in all_specs()}


def _auto_import_and_compile(p: dict) -> None:
    """Quick-import every agent's memory files, then compile if a provider is available."""
    from lorekeep.integrations.registry import all_specs

    # --- Quick import: agent-authored memory files (zero LLM cost) ---------
    for spec in all_specs():
        if spec.memory is None or spec.memory_ns is None:
            continue
        try:
            written = getattr(spec.importer(), spec.memory.import_fn)(
                p["raw"], namespace=spec.memory_ns,
            )
            if written:
                typer.echo(f"  imported {len(written)} memory file(s) from {spec.label}")
        except Exception as exc:
            log.warning(
                "automatic memory import failed agent=%s error_type=%s",
                spec.name, type(exc).__name__,
                extra={"event": "init.import_failed"},
            )
            if os.environ.get("LOREKEEP_DEBUG"):
                typer.echo(f"  import error ({spec.name}): {exc}")

    # --- Compile (if provider is usable) ----------------------------------
    schema = load_schema(p["schema"])
    config = load_config(p["config"])

    has_key = _has_provider(config)

    if not has_key:
        env_hint = ""
        if config.provider.api_key_env:
            env_hint = f" (export {config.provider.api_key_env}=sk-... before running `lorekeep compile`)"
        elif not config.provider.api_key:
            env_hint = " (add api_key to config.yaml, then run `lorekeep compile`)"
        typer.echo(
            f"  docs saved to raw/ but not yet compiled{env_hint}"
        )
        return

    try:
        provider = _make_provider(config)
        with _progress_ctx(p["raw"], config.compile.chunk_lines) as handle:
            manifest = compile_graph(
                raw_root=p["raw"], out_dir=p["out"], schema=schema,
                provider=provider, cache_path=p["cache"],
                chunk_lines=config.compile.chunk_lines,
                on_progress=_progress_cb(handle),
                personal_ns=config.ns.personal_namespace,
            )
        _report_compile_errors(manifest, exit_on_total_failure=False)
        _report_content_quality(manifest)
        pending_dir = p.get("pending")
        resolved = False
        if pending_dir and pending_dir.exists():
            resolved = _do_auto_resolve(
                p["out"], pending_dir, p.get("wiki"), p.get("schema"),
                replay_accepted=True,
            )
        if not resolved:
            _auto_generate_wiki(p["out"], p["wiki"], p.get("schema"))
        typer.echo(f"  compiled: {manifest.node_count} nodes, {manifest.edge_count} edges")
    except Exception as exc:
        log.exception(
            "initial compile failed error_type=%s", type(exc).__name__,
            extra={"event": "init.compile_failed"},
        )
        typer.echo(f"  compile skipped: {exc}")


def _start_daemon(p: dict) -> None:
    """Start agent watch as a background process with PID + log files."""
    import subprocess
    import sys

    pid_path = p["home"] / ".daemon.pid"
    log_dir = p.get("logs", p["home"] / "logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "daemon-bootstrap.log"

    # Check if already running
    if pid_path.exists():
        old_pid = pid_path.read_text().strip()
        try:
            os.kill(int(old_pid), 0)
            typer.echo(f"  daemon already running (pid={old_pid})")
            return
        except (ProcessLookupError, ValueError):
            pass

    cmd = [sys.executable, "-m", "lorekeep.cli", "agent", "watch", "--interval", "60"]
    log_file = open(log_path, "a")
    try:
        os.chmod(log_path, 0o600)
    except OSError:
        pass
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    finally:
        log_file.close()
    pid_path.write_text(str(proc.pid))
    log.info(
        "background daemon started pid=%s", proc.pid,
        extra={"event": "daemon.background_started"},
    )
    typer.echo(f"  daemon started (pid={proc.pid}, log={log_path})")


@app.command()
def backup(
    init_remote: str = typer.Option(
        None, "--init", help="remote URL; sets up the backup repo + initial push"
    ),
) -> None:
    """Commit + push the data home to your private backup repo."""
    from lorekeep.backup import BackupError, backup as backup_home, init_backup
    from lorekeep.output import dim, error, info, ok

    home = resolve_paths()["home"]
    try:
        if init_remote:
            init_backup(home, init_remote)
            info(f"backup: repo ready at {home} -> {init_remote}")
        else:
            pushed = backup_home(home)
            if pushed:
                ok(f"backup: pushed to remote from {home}")
            else:
                dim(f"backup: up to date (no changes at {home})")
    except BackupError as exc:
        error(f"backup failed: {exc}")
        raise typer.Exit(code=1)


@app.command("import")
def import_cmd(
    from_source: str = typer.Option(
        "claude", "--from",
        help="Source to import from (claude | codex | cursor | opencode)",
    ),
    quick: bool = typer.Option(
        False, "--quick",
        help="Quick mode: only import memory files, no LLM transcript analysis",
    ),
    session_path: str | None = typer.Option(
        None, "--session-path",
        help="Path to Claude session dir (auto-detect if omitted)",
    ),
    memory_ns: str = typer.Option(
        "claude-memory", "--memory-ns",
        help="Namespace for imported memory files",
    ),
    session_ns: str | None = typer.Option(
        None, "--session-ns",
        help="Namespace for imported session files (default: claude-session | cursor-session)",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be imported without writing files",
    ),
) -> None:
    """Import knowledge from an agent's sessions into raw/.

    Sources:
      claude    Claude Code sessions. --quick copies memory/*.md only (no LLM);
                default (deep) adds LLM-summarized transcript analysis.
      cursor    Cursor composer conversations (GLOBAL state.vscdb). Deep-only.
      codex     Codex CLI rollout transcripts ($CODEX_HOME/sessions/).
                --quick copies memories/*.md only; default (deep) summarizes.
      opencode  opencode sessions (SQLite DB). Deep-only — no memory dir.
    """
    from lorekeep.output import ok
    from lorekeep.integrations.registry import AGENT_NAMES
    if from_source not in AGENT_NAMES:
        typer.echo(f"unknown source: {from_source} ({' | '.join(AGENT_NAMES)})")
        raise typer.Exit(code=1)

    p = resolve_paths()
    config = load_config(p["config"])

    # --- Codex: rollout JSONL transcripts, quick + deep --------------------
    if from_source == "codex":
        from lorekeep.importer.codex import find_current_session as find_codex, import_codex

        rollout_path = Path(session_path).expanduser() if session_path else None
        if rollout_path is None:
            rollout_path = find_codex()
        if rollout_path is None and not quick:
            typer.echo("error: no Codex session found for this project. "
                       "Run Codex CLI here first, or pass --session-path.")
            raise typer.Exit(code=1)

        result = import_codex(
            raw_root=p["raw"],
            rollout_path=rollout_path,
            quick=quick,
            memory_ns=memory_ns,
            session_ns=session_ns or "codex-session",
            provider=None if quick else _make_import_provider(config),
            dry_run=dry_run,
        )
        mem_count = len(result.get("memory", []))
        ses_count = len(result.get("session", []))
        if dry_run:
            typer.echo(f"dry-run: would import {mem_count} memories, {ses_count} session files")
        else:
            ok(f"imported: {mem_count} memories -> raw/{memory_ns}/, "
               f"{ses_count} session files -> raw/{session_ns or 'codex-session'}/")
        return

    # --- opencode: SQLite DB, deep-only ------------------------------------
    if from_source == "opencode":
        if quick:
            typer.echo("error: opencode import is deep-only (--quick not supported)")
            raise typer.Exit(code=1)

        from lorekeep.importer.opencode import find_current_session as find_oc, import_opencode

        sid = session_path or find_oc()
        if sid is None:
            typer.echo("error: no opencode session found for this project. "
                       "Run opencode here first, or pass --session-path <session-id>.")
            raise typer.Exit(code=1)

        ns = session_ns or "opencode-session"
        result = import_opencode(
            raw_root=p["raw"],
            session_id=sid,
            session_ns=ns,
            provider=_make_import_provider(config),
            dry_run=dry_run,
        )
        ses_count = len(result.get("session", []))
        if dry_run:
            typer.echo(f"dry-run: would import {ses_count} opencode session files")
        else:
            ok(f"imported: {ses_count} session files -> raw/{ns}/")
            typer.echo("next: lorekeep compile")
        return

    # --- Cursor: global composer conversations, deep-only ------------------
    if from_source == "cursor":
        if quick:
            typer.echo("error: cursor import is deep-only (--quick not supported)")
            raise typer.Exit(code=1)

        from lorekeep.importer.cursor import find_cursor_state_db, import_cursor

        if session_path:
            sp = Path(session_path).expanduser()
            db = sp if sp.is_file() else sp / "state.vscdb"
            if not db.is_file():
                typer.echo(f"error: no Cursor state.vscdb at {session_path}")
                raise typer.Exit(code=1)
        else:
            db = find_cursor_state_db()
            if db is None:
                typer.echo("error: Cursor state.vscdb not found; set CURSOR_STATE_DB "
                           "or pass --session-path")
                raise typer.Exit(code=1)

        ns = session_ns or "cursor-session"
        result = import_cursor(
            raw_root=p["raw"], db_path=db, namespace=ns,
            provider=_make_import_provider(config), dry_run=dry_run,
        )
        ses_count = len(result.get("session", []))
        if dry_run:
            typer.echo(f"dry-run: would import {ses_count} cursor session files")
        else:
            ok(f"imported: {ses_count} session files -> raw/{ns}/")
            typer.echo("next: lorekeep compile")
        return

    # --- Claude: per-project session dir, quick + deep ---------------------
    if session_path:
        session_dir = Path(session_path).expanduser()
        if not session_dir.exists():
            typer.echo(f"error: no Claude session found at {session_dir}")
            raise typer.Exit(code=1)
    else:
        from lorekeep.importer.claude import find_current_session
        session_dir = find_current_session()
        if session_dir is None:
            typer.echo("error: no Claude session found. "
                       "Run Claude Code in this project first.")
            raise typer.Exit(code=1)

    provider = None if quick else _make_import_provider(config)

    from lorekeep.importer.claude import import_claude
    result = import_claude(
        raw_root=p["raw"],
        session_dir=session_dir,
        quick=quick,
        memory_ns=memory_ns,
        session_ns=session_ns or "claude-session",
        provider=provider,
        dry_run=dry_run,
    )

    mem_count = len(result.get("memory", []))
    ses_count = len(result.get("session", []))
    if dry_run:
        typer.echo(f"dry-run: would import {mem_count} memories, "
                   f"{ses_count} session files")
    else:
        ok(f"imported: {mem_count} memories -> raw/{memory_ns}/, "
           f"{ses_count} session files -> raw/{session_ns}/")
        if not quick:
            typer.echo("next: lorekeep compile")


# --- Agent subcommands --------------------------------------------------------

service_app = typer.Typer(help="Install/uninstall the daemon as a persistent OS service.")
agent_app.add_typer(service_app, name="service")


@service_app.command("install")
def daemon_install() -> None:
    """Install daemon as a persistent OS service (survives restart).

    Linux: systemd user service. macOS: launchd LaunchAgent. Windows: Startup folder.

    The service runs `lorekeep agent watch` in the background.
    """
    from lorekeep.daemon_service import install as svc_install
    p = resolve_paths()
    try:
        platform_name, config_path = svc_install(p["home"])
        log.info(
            "daemon service installed platform=%s", platform_name,
            extra={"event": "daemon.service_installed"},
        )
        typer.echo(f"daemon: installed as {platform_name} service → {config_path}")
        typer.echo(f"daemon: will auto-start on login/restart")
    except RuntimeError as exc:
        log.error(
            "daemon service install failed error_type=%s", type(exc).__name__,
            extra={"event": "daemon.service_install_failed"},
        )
        typer.echo(f"daemon: {exc}")
        raise typer.Exit(code=1)


@service_app.command("uninstall")
def daemon_uninstall() -> None:
    """Remove the persistent daemon service."""
    from lorekeep.daemon_service import uninstall as svc_uninstall
    removed = svc_uninstall()
    log.info(
        "daemon service uninstall completed removed=%s", removed,
        extra={"event": "daemon.service_uninstalled"},
    )
    if removed:
        typer.echo("daemon: service removed")
    else:
        typer.echo("daemon: no service found")


@service_app.command("status")
def daemon_status() -> None:
    """Check if the persistent daemon service is installed and running."""
    from lorekeep.daemon_service import status as svc_status
    state = svc_status()
    log.info("daemon service status checked", extra={"event": "daemon.service_status"})
    typer.echo(state)


@agent_app.command()
def ingest(
    source: str = typer.Argument(
        ..., help="Path to a source .md file under raw/",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y",
        help="Approve all extracted facts without review",
    ),
) -> None:
    """Conversational ingest: read a source, extract facts via LLM, review and journal.

    This is the Karpathy Ingest operation: the LLM reads the source, extracts
    key facts (nodes and edges), and the human reviews them before they enter
    the knowledge graph via the pending journal.

    Run `lorekeep resolve` or `lorekeep compile` afterwards to merge approved
    facts into facts.jsonl.
    """
    from datetime import datetime, timezone

    p = resolve_paths()
    schema = load_schema(p["schema"])
    config = load_config(p["config"])
    raw_root = p["raw"]
    pending_dir = p.get("pending")
    if pending_dir is None:
        typer.echo("ingest: no pending directory configured")
        raise typer.Exit(code=1)

    source_path = Path(source).expanduser()
    if not source_path.is_absolute():
        source_path = Path.cwd() / source_path
    source_path = source_path.resolve()

    if not source_path.exists():
        typer.echo(f"ingest: source not found: {source_path}")
        raise typer.Exit(code=1)

    if not source_path.is_relative_to(raw_root.resolve()):
        typer.echo(f"ingest: source must be under raw/ ({raw_root})")
        raise typer.Exit(code=1)

    provider = _make_provider(config)

    from contextlib import nullcontext
    from lorekeep.agent import ingest_source
    from lorekeep.output import is_quiet, is_terminal, progress

    if not is_quiet() and is_terminal():
        cm = progress(f"Extracting {source_path.name}", total=None)
    else:
        cm = nullcontext(None)
    with cm as handle:
        on_progress = None
        if handle:
            def _cb(i, total, chunk, _h=handle):
                if total:
                    _h.update(total=total)
                _h.advance()
            on_progress = _cb
        try:
            result = ingest_source(
                source_path=source_path,
                raw_root=raw_root,
                provider=provider,
                schema=schema,
                chunk_lines=config.compile.chunk_lines,
                on_progress=on_progress,
                personal_ns=config.ns.personal_namespace,
            )
        except Exception as exc:
            typer.echo(f"ingest: extraction failed: {exc}")
            raise typer.Exit(code=1)

    if not result.nodes and not result.edges:
        typer.echo("ingest: no facts extracted from source")
        return

    # Show extracted facts
    typer.echo(f"\nSource: {result.source_path}  (ns={result.ns}, chunks={result.chunk_count})")
    typer.echo(f"Extracted: {len(result.nodes)} nodes, {len(result.edges)} edges\n")

    for n in result.nodes:
        props_str = ", ".join(f"{k}={v}" for k, v in n.get("props", {}).items())
        vf = n.get("valid_from", "")
        vt = n.get("valid_to", "")
        valid = f" [{vf}..{vt}]" if vf or vt else ""
        typer.echo(f"  NODE: {n['id']} ({n['type']}){valid}")
        if props_str:
            typer.echo(f"    {props_str}")

    for e in result.edges:
        vf = e.get("valid_from", "")
        vt = e.get("valid_to", "")
        valid = f" [{vf}..{vt}]" if vf or vt else ""
        typer.echo(f"  EDGE: {e['from']} --[{e['type']}]--> {e['to']}{valid}")

    # Interactive review (unless --yes)
    approved_nodes: list[dict] = []
    approved_edges: list[dict] = []

    if yes:
        approved_nodes = list(result.nodes)
        approved_edges = list(result.edges)
    else:
        typer.echo("")
        if result.nodes:
            if typer.confirm(f"Approve all {len(result.nodes)} nodes?", default=True):
                approved_nodes = list(result.nodes)
            elif typer.confirm("Review each node individually?", default=True):
                for n in result.nodes:
                    props_str = ", ".join(f"{k}={v}" for k, v in n.get("props", {}).items())
                    line = f"  {n['id']} ({n['type']}) — {props_str}"
                    if typer.confirm(f"Approve? {line}", default=True):
                        approved_nodes.append(n)

        if result.edges:
            if typer.confirm(f"Approve all {len(result.edges)} edges?", default=True):
                approved_edges = list(result.edges)
            elif typer.confirm("Review each edge individually?", default=True):
                for e in result.edges:
                    line = f"  {e['from']} --[{e['type']}]--> {e['to']}"
                    if typer.confirm(f"Approve? {line}", default=True):
                        approved_edges.append(e)

    if not approved_nodes and not approved_edges:
        typer.echo("\ningest: nothing approved — no journal entries written")
        return

    # Write approved facts to journal
    from lorekeep.journal import append_journal
    from lorekeep.models import JournalEntry

    import socket
    import uuid
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    device = os.environ.get("LOREKEEP_DEVICE", socket.gethostname())
    entry_count = 0

    for n in approved_nodes:
        n["src"] = list(n.get("src", []))
        if result.source_path not in n["src"]:
            n["src"].append(result.source_path)
        entry = JournalEntry(
            fact=n,
            agent="cli-ingest",
            device=device,
            entry_id=uuid.uuid4().hex,
            ns=result.ns,
            confidence=1.0,           # human-approved → max confidence
            proposed_at=now,
            status="pending",
        )
        append_journal(pending_dir, entry, result.ns)
        entry_count += 1

    for e in approved_edges:
        e["src"] = list(e.get("src", []))
        if result.source_path not in e["src"]:
            e["src"].append(result.source_path)
        entry = JournalEntry(
            fact=e,
            agent="cli-ingest",
            device=device,
            entry_id=uuid.uuid4().hex,
            ns=result.ns,
            confidence=1.0,
            proposed_at=now,
            status="pending",
        )
        append_journal(pending_dir, entry, result.ns)
        entry_count += 1

    typer.echo(f"\ningest: {entry_count} facts written to pending/{result.ns}/journal.jsonl")
    typer.echo("next: run `lorekeep resolve` to merge into facts.jsonl")


@agent_app.command()
def lint(
    auto_fix: bool = typer.Option(
        False, "--auto-fix",
        help="Auto-apply high-confidence fixes",
    ),
    focus: str = typer.Option(
        None, "--focus",
        help="Lint a specific entity by id",
    ),
) -> None:
    """Run semantic health checks on the graph.

    See also: `lorekeep doctor` for structural validation and full install checks.
    """
    p = resolve_paths()
    facts_path = p["out"] / "facts.jsonl"
    if not facts_path.exists():
        typer.echo("lint: no graph — run `lorekeep compile` first")
        raise typer.Exit(code=1)

    from lorekeep.store.graph import GraphStore
    from lorekeep.agent import lint as agent_lint
    store = GraphStore.from_jsonl(facts_path)
    report = agent_lint(store)

    if focus:
        report.orphans = [o for o in report.orphans if o == focus]
        report.stale = [s for s in report.stale if s == focus]
        report.missing_endpoints = [m for m in report.missing_endpoints
                                     if m["edge_id"] == focus]

    if not report.has_issues:
        typer.echo("lint: no issues found")
        return

    if report.contradictions:
        typer.echo(f"contradictions: {len(report.contradictions)}")
        for c in report.contradictions[:10]:
            typer.echo(f"  {c['id']}")
    if report.orphans:
        typer.echo(f"orphans: {len(report.orphans)}")
        for o in report.orphans[:10]:
            typer.echo(f"  {o}")
    if report.stale:
        typer.echo(f"stale: {len(report.stale)}")
    if report.missing_endpoints:
        typer.echo(f"missing endpoints: {len(report.missing_endpoints)}")
    if report.coverage_gaps:
        typer.echo(f"coverage gaps: {report.coverage_gaps}")

    if auto_fix:
        typer.echo("auto-fix: not yet implemented (planned)")

    typer.echo(f"total issues: {report.issue_count}")


@agent_app.command()
def suggest() -> None:
    """Generate improvement suggestions for the graph."""
    p = resolve_paths()
    facts_path = p["out"] / "facts.jsonl"
    if not facts_path.exists():
        typer.echo("suggest: no graph — run `lorekeep compile` first")
        raise typer.Exit(code=1)

    from lorekeep.store.graph import GraphStore
    from lorekeep.agent import suggest as agent_suggest
    store = GraphStore.from_jsonl(facts_path)
    report = agent_suggest(store)

    if report.gaps:
        typer.echo("knowledge gaps:")
        for g in report.gaps:
            typer.echo(f"  {g}")
    if report.under_sourced:
        typer.echo(f"under-sourced entities: {len(report.under_sourced)}")
        for u in report.under_sourced[:10]:
            typer.echo(f"  {u}")
    if report.suggestions:
        typer.echo("suggestions:")
        for s in report.suggestions:
            typer.echo(f"  {s}")

    if not report.gaps and not report.under_sourced and not report.suggestions:
        typer.echo("suggest: no suggestions (graph looks healthy)")


@agent_app.command()
def status() -> None:
    """Print a graph health dashboard."""
    p = resolve_paths()
    facts_path = p["out"] / "facts.jsonl"
    if not facts_path.exists():
        typer.echo("status: no graph — run `lorekeep compile` first")
        raise typer.Exit(code=1)

    from lorekeep.store.graph import GraphStore
    from lorekeep.agent import agent_status
    store = GraphStore.from_jsonl(facts_path)
    dash = agent_status(store, p.get("pending"))

    typer.echo(f"nodes: {dash.node_count}")
    typer.echo(f"edges: {dash.edge_count}")
    typer.echo(f"namespaces: {dash.namespace_count} ({', '.join(dash.namespaces)})")
    typer.echo(f"lint issues: {dash.lint_issues}")
    typer.echo(f"pending journals: {dash.pending_journals}")


def _discover_watchable_sessions() -> list[tuple[str, Path, Path]]:
    """Find agent memory dirs that support zero-LLM quick import.

    Returns [(agent_name, session_dir, memory_dir), ...]. Only agents whose
    spec declares a memory source appear here — transcript-only agents are
    handled by the session dump step.
    """
    from lorekeep.integrations.registry import all_specs

    sessions: list[tuple[str, Path, Path]] = []
    for spec in all_specs():
        if spec.memory is None:
            continue
        try:
            mem_dir = getattr(spec.importer(), spec.memory.dir_finder)()
            if mem_dir and any(mem_dir.glob("*.md")):
                sessions.append((spec.name, mem_dir.parent, mem_dir))
        except Exception as exc:
            log.warning(
                "session discovery failed agent=%s error_type=%s",
                spec.name, type(exc).__name__,
                extra={"event": "daemon.session_discovery_failed"},
            )

    return sessions


def _quick_import_session(agent: str, session_dir: Path, memory_dir: Path, raw_dir: Path) -> int:
    """Quick-import memory files for one agent. Returns file count."""
    from lorekeep.integrations.registry import find

    spec = find(agent)
    if spec is None or spec.memory is None or spec.memory_ns is None:
        return 0
    written = getattr(spec.importer(), spec.memory.import_fn)(
        raw_dir, namespace=spec.memory_ns, memory_dir=memory_dir,
    )
    return len(written)


def _on_disk_version() -> str | None:
    """Read lorekeep's version from installed package metadata on disk.

    Called by the daemon loop to detect upgrades: the running process has
    ``__version__`` in memory, but ``importlib.metadata.version()`` reads
    from the dist-info dir. When ``uv tool upgrade`` installs a new version,
    the on-disk value changes while the process keeps the old code.
    """
    import importlib
    import importlib.metadata
    importlib.invalidate_caches()
    try:
        return importlib.metadata.version("lorekeep")
    except importlib.metadata.PackageNotFoundError:
        return None


@agent_app.command()
def watch(
    interval: int = typer.Option(
        60, "--interval",
        help="Polling interval in seconds",
    ),
    watch_sessions: bool = typer.Option(
        True, "--watch-sessions/--no-watch-sessions",
        help="Watch agent session dirs for live continuous ingest",
    ),
) -> None:
    """Run the autonomous agent daemon: watch raw/, pending/, and agent sessions.

    Watches raw/ for new/changed markdown → auto-compile.
    Watches pending/ for new journal entries → auto-resolve.
    Watches Claude + Codex memory dirs → delta quick import → raw/.
    Cursor/opencode are handled by session-end hooks (`lorekeep hook`).

    For unattended operation, use `lorekeep agent service install` to run this
    as a background OS service.
    """
    import signal
    import sys
    import time

    p = resolve_paths()
    raw_dir = p["raw"]
    pending_dir = p.get("pending")

    typer.echo(f"agent watch: monitoring raw={raw_dir}, pending={pending_dir}, interval={interval}s")
    typer.echo("agent: auto-compile (raw/) and auto-resolve (pending/) enabled")
    if watch_sessions:
        typer.echo("agent: session watch enabled (Claude + Codex memory dirs)")
    typer.echo("agent: MCP server lazy-reloads facts.jsonl — no reconnect needed")
    log.info(
        "daemon watch started interval=%s session_watch=%s",
        interval, watch_sessions, extra={"event": "daemon.start"},
    )

    pid_file = p["home"] / ".daemon.pid"
    if pid_file.exists():
        try:
            old_pid = int(pid_file.read_text().strip())
            os.kill(old_pid, 0)
            typer.echo(f"agent: daemon already running (PID {old_pid}), exiting")
            raise typer.Exit(code=1)
        except (ProcessLookupError, ValueError, PermissionError):
            pass
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()))

    # --- SIGTERM handler: clean PID file on kill / systemctl stop -------------
    def _on_sigterm(signum, frame):
        pid_file.unlink(missing_ok=True)
        log.info("daemon received SIGTERM — shutting down", extra={"event": "daemon.sigterm"})
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _on_sigterm)

    # --- Capture startup version for auto-restart-on-upgrade -----------------
    startup_version = _on_disk_version()

    last_raw_mtime = 0.0
    last_raw_count = -1
    last_pending_mtime = 0.0
    last_schema_mtime = 0.0
    session_state: dict[str, float] = {}
    session_import_time: dict[str, float] = {}

    # Sync from remote at startup (pull changes from other machines)
    try:
        from lorekeep.backup import sync_backup, has_remote
        if has_remote(p["home"]):
            typer.echo("agent: syncing backup from remote...")
            sync_backup(p["home"])
    except Exception as exc:
        log.warning(
            "startup backup sync failed error_type=%s", type(exc).__name__,
            extra={"event": "daemon.backup_failed"},
        )

    # Resolve/replay only after startup sync so remote journal events are visible.
    if pending_dir and pending_dir.exists():
        from lorekeep.journal import load_journals
        journals = load_journals(pending_dir)
        if any(j.status in {"pending", "merged", "flagged"} for j in journals):
            typer.echo("agent: replaying journals at startup...")
            _do_auto_resolve(
                p["out"], pending_dir, p.get("wiki"), p.get("schema"),
                replay_accepted=True,
            )

    while True:
        try:
            # --- auto-restart on upgrade ---------------------------------------
            # If lorekeep was upgraded (pip/uv) while this daemon is running,
            # the on-disk version changes but the process still runs old code.
            # Detect and hot-swap via os.execv (systemd/launchd won't notice).
            current_version = _on_disk_version()
            if startup_version is not None and current_version is not None and current_version != startup_version:
                log.info(
                    "lorekeep upgraded (%s → %s) — restarting daemon",
                    startup_version, current_version,
                    extra={"event": "daemon.upgrade_restart"},
                )
                typer.echo(f"agent: upgraded {startup_version} → {current_version}, restarting...")
                pid_file.unlink(missing_ok=True)
                os.execv(sys.argv[0], sys.argv)

            # Re-check existence each cycle (raw/ or pending/ may be created after start)
            has_raw = raw_dir.exists()
            has_pending = pending_dir and pending_dir.exists()
            # --- raw/ + schema watch → auto-compile ---------------------------
            raw_files = sorted(raw_dir.rglob("*.md")) if has_raw else []
            raw_mtime = max((f.stat().st_mtime for f in raw_files), default=0.0)
            raw_count = len(raw_files)
            schema_mtime = p["schema"].stat().st_mtime if p["schema"].exists() else 0.0
            compiled = False

            should_compile = False
            if last_raw_count >= 0:
                if raw_count != last_raw_count:
                    should_compile = True
                elif raw_mtime > last_raw_mtime:
                    should_compile = True
            if last_schema_mtime > 0 and schema_mtime > last_schema_mtime:
                should_compile = True

            if should_compile:
                typer.echo(f"agent: raw/ changed ({raw_count} files) — compiling...")
                try:
                    schema = load_schema(p["schema"])
                    config = load_config(p["config"])
                    provider = _make_provider(config)
                    with _progress_ctx(raw_dir, config.compile.chunk_lines) as handle:
                        dm = compile_graph(
                            raw_root=raw_dir, out_dir=p["out"], schema=schema,
                            provider=provider, cache_path=p["cache"],
                            chunk_lines=config.compile.chunk_lines,
                            on_progress=_progress_cb(handle),
                            personal_ns=config.ns.personal_namespace,
                        )
                    _report_compile_errors(dm, exit_on_total_failure=False)
                    _report_content_quality(dm)
                    typer.echo("agent: compile done")
                    compiled = True
                except Exception as exc:
                    log.exception(
                        "daemon compile failed error_type=%s", type(exc).__name__,
                        extra={"event": "daemon.compile_failed"},
                    )
                    typer.echo(f"agent: compile error: {exc}")
            last_raw_mtime = raw_mtime
            last_raw_count = raw_count
            last_schema_mtime = schema_mtime

            # --- auto-backup + sync after compile ---------------------------
            if compiled:
                try:
                    from lorekeep.backup import sync_backup
                    if sync_backup(p["home"]):
                        typer.echo("agent: backup synced")
                except Exception as exc:
                    log.warning(
                        "post-compile backup sync failed error_type=%s",
                        type(exc).__name__, extra={"event": "daemon.backup_failed"},
                    )
                resolved = False
                if has_pending:
                    resolved = _do_auto_resolve(
                        p["out"], pending_dir, p.get("wiki"), p.get("schema"),
                        replay_accepted=True,
                    )
                if not resolved:
                    _auto_generate_wiki(p["out"], p.get("wiki"), p.get("schema"))

            # --- pending/ watch → auto-resolve ------------------------------
            if has_pending:
                journal_files = sorted(pending_dir.rglob("journal.jsonl"))
                pending_mtime = max((f.stat().st_mtime for f in journal_files), default=0.0)
                if pending_mtime > last_pending_mtime and last_pending_mtime > 0:
                    typer.echo("agent: pending/ changed — resolving...")
                    _do_auto_resolve(
                        p["out"], pending_dir, p.get("wiki"), p.get("schema"),
                    )
                last_pending_mtime = pending_mtime

            # --- session watch → delta quick import → raw/ ------------------
            # Re-discover every cycle (cheap — just directory scans).
            # Detects new sessions opened after daemon start.
            if watch_sessions:
                now = time.monotonic()
                sessions = _discover_watchable_sessions()
                for agent_name, session_dir, memory_dir in sessions:
                    mem_files = sorted(memory_dir.glob("*.md"))
                    mem_mtime = max((f.stat().st_mtime for f in mem_files), default=0.0)
                    prev = session_state.get(agent_name, 0.0)
                    last_import = session_import_time.get(agent_name, 0.0)

                    if (mem_mtime > prev and prev > 0
                            and now - last_import >= 30):
                        typer.echo(f"agent: {agent_name} memory changed ({len(mem_files)} files) — importing...")
                        try:
                            count = _quick_import_session(agent_name, session_dir, memory_dir, raw_dir)
                            if count:
                                typer.echo(f"agent: {agent_name} import done — {count} files → raw/{agent_name}-memory/")
                                session_import_time[agent_name] = now
                        except Exception as exc:
                            log.exception(
                                "session import failed agent=%s error_type=%s",
                                agent_name, type(exc).__name__,
                                extra={"event": "daemon.session_import_failed"},
                            )
                            typer.echo(f"agent: {agent_name} import error: {exc}")
                    session_state[agent_name] = mem_mtime

            time.sleep(interval)
        except KeyboardInterrupt:
            log.info("daemon watch stopped", extra={"event": "daemon.stop"})
            typer.echo("\nagent: shutting down")
            break
        except Exception as exc:
            log.exception(
                "daemon loop failed error_type=%s", type(exc).__name__,
                extra={"event": "daemon.loop_failed"},
            )
            typer.echo(f"agent: error: {exc}")
            time.sleep(interval)

    pid_file.unlink(missing_ok=True)


def _do_auto_resolve(
    out_dir: Path,
    pending_dir: Path,
    wiki_dir: Path | None = None,
    schema_path: Path | None = None,
    replay_accepted: bool = False,
) -> bool:
    from lorekeep.journal import resolve_lock

    with resolve_lock(pending_dir):
        return _do_auto_resolve_unlocked(
            out_dir,
            pending_dir,
            wiki_dir,
            schema_path,
            replay_accepted,
        )


def _do_auto_resolve_unlocked(
    out_dir: Path,
    pending_dir: Path,
    wiki_dir: Path | None = None,
    schema_path: Path | None = None,
    replay_accepted: bool = False,
) -> bool:
    """Merge pending journal entries into facts.jsonl.

    Extracted as a helper so both the pending/ watch loop and the
    post-compile re-merge path share the same logic.

    Returns True if facts.jsonl was rewritten (merge happened).
    """
    try:
        from lorekeep.facts_io import read_facts
        from lorekeep.compile.resolve import resolve as resolve_facts, merge_journals
        from lorekeep.compile.writer import write_graph
        from lorekeep.journal import load_journals, update_journal_status
        from lorekeep.models import Edge, Manifest, Node
        from lorekeep.pipeline import measure_content_quality

        facts_path = out_dir / "facts.jsonl"
        existing_nodes = []
        existing_edges = []
        if facts_path.exists():
            for f in read_facts(facts_path):
                if isinstance(f, Node):
                    existing_nodes.append(f)
                else:
                    existing_edges.append(f)

        journals = load_journals(pending_dir)
        candidate_entries = [
            j for j in journals
            if j.status == "pending"
            or (replay_accepted and j.status in {"merged", "flagged"})
        ]
        if candidate_entries:
            schema = load_schema(schema_path) if schema_path else None
            merged = merge_journals(
                existing_nodes,
                existing_edges,
                candidate_entries,
                replay_accepted=replay_accepted,
                schema=schema,
            )
            resolved = resolve_facts(merged.nodes, merged.edges, schema=schema)
            manifest = Manifest(
                schema_version=schema.version if schema else 0, chunk_count=0,
                node_count=len(resolved.nodes),
                edge_count=len(resolved.edges),
                run_id="auto-resolve", facts_hash="",
                compiled_at=now_iso(),
                merged_count=merged.merge_count,
                quarantined_count=merged.quarantine_count,
                flagged_count=merged.flagged_count,
                content_quality=(
                    measure_content_quality(resolved.nodes, resolved.edges, schema)
                    if schema else None
                ),
            )
            write_graph(out_dir, resolved.nodes, resolved.edges, manifest)

            for ns in set(entry.ns for entry, _ in merged.merged):
                entry_keys = {
                    e.entry_id or e.proposed_at
                    for e, _ in merged.merged if e.ns == ns
                }
                if entry_keys:
                    update_journal_status(pending_dir, ns, entry_keys, "merged")
            for ns in set(entry.ns for entry, _ in merged.flagged):
                entry_keys = {
                    e.entry_id or e.proposed_at
                    for e, _ in merged.flagged if e.ns == ns
                }
                existing = {
                    e.entry_id or e.proposed_at
                    for e, _ in merged.merged if e.ns == ns
                }
                to_flag = entry_keys - existing
                if to_flag:
                    update_journal_status(pending_dir, ns, to_flag, "flagged")

            typer.echo(f"agent: resolve done — {merged.merge_count} merged, "
                       f"{merged.flagged_count} flagged, {merged.quarantine_count} quarantined")
            log.info(
                "auto-resolve completed merged=%s flagged=%s quarantined=%s",
                merged.merge_count, merged.flagged_count, merged.quarantine_count,
                extra={"event": "resolve.complete"},
            )

            if wiki_dir:
                _auto_generate_wiki(out_dir, wiki_dir, schema_path)
            return True
    except Exception as exc:
        log.exception(
            "auto-resolve failed error_type=%s", type(exc).__name__,
            extra={"event": "resolve.failed"},
        )
        typer.echo(f"agent: resolve error: {exc}")
    return False


def _auto_generate_wiki(
    graph_dir: Path,
    wiki_dir: Path,
    schema_path: Path | None = None,
) -> None:
    """Regenerate wiki after compile or resolve. Best-effort, never blocks."""
    try:
        from lorekeep.wiki import generate_wiki
        schema = load_schema(schema_path) if schema_path and schema_path.exists() else None
        generate_wiki(graph_dir, wiki_dir, schema=schema)
        log.info("wiki generated", extra={"event": "wiki.complete"})
    except Exception as exc:
        log.warning(
            "wiki generation skipped error_type=%s", type(exc).__name__,
            extra={"event": "wiki.failed"},
        )
        typer.echo(f"wiki: auto-gen skipped: {exc}")


if __name__ == "__main__":
    app()
