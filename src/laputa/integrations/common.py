"""Shared integration helpers: resolve install command + agent-memory snippet."""
from __future__ import annotations


def resolve_command(install_source: str | None) -> tuple[str, list[str]]:
    """Return (command, args) to launch `laputa serve --transport stdio`."""
    serve_args = ["serve", "--transport", "stdio"]
    if not install_source or install_source == "pypi":
        return ("uvx", ["laputa", *serve_args])
    if install_source == "local":
        return ("laputa", serve_args)
    # anything else (git+URL, local path) -> uvx --from <source>
    return ("uvx", ["--from", install_source, "laputa", *serve_args])


def agent_memory_snippet() -> str:
    return (
        "## Laputa knowledge base (MCP)\n"
        "Before answering architecture/code/domain questions, query Laputa:\n"
        "search(q) -> get_node(id) -> neighbors / at_time / history as needed.\n"
        "Always cite `src` provenance. Knowledge is namespace-scoped - if a fact is\n"
        "missing, it may be outside your scope, not nonexistent.\n"
    )
