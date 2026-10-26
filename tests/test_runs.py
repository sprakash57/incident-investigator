from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic_ai.models.test import TestModel

from incident_investigator.agent.deps import InvestigationDeps
from incident_investigator.runs import RunFile, run_and_record
from incident_investigator.telemetry.sqlite import SQLiteBackend
from tests.test_agent import report_args, scripted

NOW = datetime(2026, 3, 10, 9, 0, tzinfo=UTC)


@pytest.fixture
def deps(demo_db: Path) -> InvestigationDeps:
    return InvestigationDeps(backend=SQLiteBackend(demo_db), now=NOW)


def test_successful_run_round_trips(deps: InvestigationDeps, tmp_path: Path) -> None:
    model = scripted(
        ("record_plan", {"steps": ["check changes"]}),
        ("get_changes", {"start": "2026-03-10T06:00:00Z", "end": "2026-03-10T09:00:00Z"}),
        ("OUTPUT", report_args(cite="t1")),
    )
    run = run_and_record("checkout is slow", deps, model=model, max_tool_calls=10)

    assert run.error is None
    assert run.report is not None
    assert run.plan[0].steps == ["check changes"]
    assert [c.tool for c in run.tool_calls] == ["get_changes"]
    assert run.usage is not None and run.usage.tool_calls == 2
    assert run.unknown_citations == []
    assert run.backend.startswith("sqlite")

    path = run.save(tmp_path / "runs")
    assert path.name == f"{run.id}.json"
    loaded = RunFile.load(path)
    assert loaded.model_dump(mode="json") == run.model_dump(mode="json")
    assert loaded.tool_calls[0].result["changes"][0]["version"] == "2.14.0"


def test_failed_run_is_still_recorded(deps: InvestigationDeps) -> None:
    run = run_and_record(
        "checkout is slow", deps, model=scripted(*[("get_topology", {})] * 5), max_tool_calls=2
    )
    assert run.report is None
    assert run.error and run.error.startswith("UsageLimitExceeded")
    assert len(run.tool_calls) == 2


def test_model_name_is_recorded(deps: InvestigationDeps) -> None:
    model = TestModel(call_tools=[], custom_output_args=report_args(cite="t1"))
    run = run_and_record("q", deps, model=model, max_tool_calls=5)
    # TestModel cannot cite a real call, so the validator keeps rejecting it until retries run out.
    assert run.error and "retries" in run.error.lower()
    assert run.model == "test"
