"""Laputa CLI."""
import typer

from laputa import __version__

app = typer.Typer(help="Laputa — compile team docs into a temporal knowledge graph.")


# Empty callback forces multi-command mode so subcommands (version, compile, ...)
# are not auto-promoted to the top level by Typer.
@app.callback()
def _main() -> None:
    """Laputa — compile team docs into a temporal knowledge graph."""


@app.command()
def version() -> None:
    """Print the Laputa version."""
    typer.echo(f"laputa {__version__}")


if __name__ == "__main__":
    app()
