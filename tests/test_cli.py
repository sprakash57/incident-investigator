from typer.testing import CliRunner

from incident_investigator import __version__
from incident_investigator.cli import app

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_config_lists_settings() -> None:
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0
    assert "backend" in result.output
