"""Laputa CLI."""
import typer

app = typer.Typer(help="Laputa — compile team docs into a temporal knowledge graph.")


@app.callback()
def _main() -> None:
    """Laputa — compile team docs into a temporal knowledge graph."""


@app.command()
def version() -> None:
    """Print the Laputa version."""
    typer.echo("laputa 0.1.0")


if __name__ == "__main__":
    app()
