from typer.testing import CliRunner
from lorekeep.cli import app

runner = CliRunner()


def test_version_command():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "lorekeep 0.1.0" in result.stdout
