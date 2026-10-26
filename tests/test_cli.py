from pathlib import Path

import pytest
from typer.testing import CliRunner

from incident_investigator import __version__, cli
from incident_investigator.cli import app
from incident_investigator.runs import RunFile
from tests.test_agent import report_args, scripted

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_config_lists_settings() -> None:
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0
    assert "backend" in result.output


@pytest.fixture
def demo_env(demo_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("INVESTIGATOR_SQLITE_PATH", str(demo_db))
    monkeypatch.setenv("INVESTIGATOR_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("INVESTIGATOR_MODEL", "scripted")
    monkeypatch.setattr(cli.console, "width", 200)  # no wrapping, so output is assertable
    return tmp_path / "runs"


def test_investigate_requires_a_model(demo_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INVESTIGATOR_MODEL")
    result = runner.invoke(app, ["investigate", "checkout is slow"])
    assert result.exit_code == 1
    assert "no model configured" in result.output


def test_investigate_explains_missing_demo_data(
    demo_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INVESTIGATOR_SQLITE_PATH", str(tmp_path / "missing.db"))
    result = runner.invoke(app, ["investigate", "checkout is slow"])
    assert result.exit_code == 1
    assert "iinv generate-data" in result.output


def test_investigate_end_to_end(
    demo_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = scripted(
        ("record_plan", {"steps": ["look at recent changes"]}),
        ("get_changes", {"start": "2026-03-10T06:00:00Z", "end": "2026-03-10T09:00:00Z"}),
        ("OUTPUT", report_args(cite="t1")),
    )
    monkeypatch.setattr(cli, "_model_for", lambda name: model)
    report_path = tmp_path / "report.md"

    result = runner.invoke(app, ["investigate", "checkout is slow", "-o", str(report_path)])

    assert result.exit_code == 0, result.output
    assert "look at recent changes" in result.output
    assert "t1 get_changes(start=03-10 06:00 end=03-10 09:00) → 1 changes" in result.output
    assert "Root cause hypothesis" in result.output
    assert "now 2026-03-10 09:00 UTC" in result.output  # capped at the end of the demo data
    (run_file,) = demo_env.glob("*.json")
    assert RunFile.load(run_file).report is not None
    saved = report_path.read_text()
    assert "## Tool calls" in saved
    assert "`[t1]`" in saved


def test_failed_investigation_exits_non_zero_but_saves_the_run(
    demo_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = scripted(*[("get_topology", {})] * 30)
    monkeypatch.setattr(cli, "_model_for", lambda name: model)
    result = runner.invoke(app, ["investigate", "checkout is slow"])
    assert result.exit_code == 1
    assert "UsageLimitExceeded" in result.output
    assert len(list(demo_env.glob("*.json"))) == 1
