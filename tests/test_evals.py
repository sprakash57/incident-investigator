from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from incident_investigator import cli
from incident_investigator.agent.report import IncidentReport
from incident_investigator.agent.trace import ToolCallRecord
from incident_investigator.evals.scenarios import SCENARIOS_BY_ID
from incident_investigator.evals.scoring import score_run
from incident_investigator.runs import RunFile
from tests.test_agent import report_args, scripted

T = datetime(2026, 3, 10, 9, 0, tzinfo=UTC)
GOOD_ROOT_CAUSE = (
    "payments v2.14.0 cut the DB connection pool from 20 to 5; once morning traffic ramped "
    "past what 5 connections can serve, requests queued and timed out."
)


def good_report_args(cite: str = "t1") -> dict[str, Any]:
    return report_args(cite) | {
        "summary": "Checkout is slow because payments is exhausting its DB connection pool.",
        "root_cause_hypothesis": GOOD_ROOT_CAUSE,
        "ruled_out": [
            {"hypothesis": "checkout feature flag new-cart-ui", "reason": "no errors in cart"}
        ],
    }


def make_run(report: dict[str, Any] | None, tools: list[str]) -> RunFile:
    calls = [
        ToolCallRecord(id=f"t{i}", tool=tool, args={}, started_at=T, result={"ok": True})
        for i, tool in enumerate(tools, 1)
    ]
    return RunFile(
        id="r1",
        question="q",
        model="m",
        backend="b",
        now=T,
        started_at=T,
        finished_at=T,
        plan=[],
        tool_calls=calls,
        report=IncidentReport.model_validate(report) if report else None,
        error=None if report else "UsageLimitExceeded: out of calls",
    )


def test_good_report_passes_every_check() -> None:
    run = make_run(good_report_args(), ["search_logs", "known_issues"])
    score = score_run(SCENARIOS_BY_ID["checkout-slow"], run)
    assert score.checks == dict.fromkeys(score.checks, True)
    assert score.passed


def test_red_herring_and_missing_mechanism_fail() -> None:
    args = good_report_args() | {
        "summary": "The new-cart-ui feature flag slowed checkout.",
        "root_cause_hypothesis": "A bug in payments v2.14.0.",
    }
    score = score_run(SCENARIOS_BY_ID["checkout-slow"], make_run(args, ["search_logs"]))
    assert not score.passed
    assert score.checks["names deploy v2.14.0"]
    assert not score.checks["mechanism: connection pool"]
    assert not score.checks["red herring not blamed"]  # mentioned outside ruled_out
    assert not score.checks["used known_issues"]


def test_failed_run_fails_all_checks() -> None:
    score = score_run(SCENARIOS_BY_ID["checkout-slow"], make_run(None, []))
    assert not score.completed
    assert not any(score.checks.values())


def test_eval_command_end_to_end(
    demo_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INVESTIGATOR_SQLITE_PATH", str(demo_db))
    monkeypatch.setenv("INVESTIGATOR_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("INVESTIGATOR_MODEL", "scripted")
    monkeypatch.setattr(cli.console, "width", 200)
    monkeypatch.setattr(
        cli,
        "_model_for",
        lambda name: scripted(
            (
                "search_logs",
                {"start": "2026-03-10T06:00:00Z", "end": "2026-03-10T09:00:00Z", "limit": 5},
            ),
            ("known_issues", {}),
            ("OUTPUT", good_report_args("t1")),
        ),
    )
    result = CliRunner().invoke(cli.app, ["eval", "--scenario", "checkout-slow", "--repeats", "2"])
    assert result.exit_code == 0, result.output
    assert result.output.count("PASS checkout-slow") == 2
    (out_dir,) = (tmp_path / "runs" / "evals").iterdir()
    assert len(list(out_dir.glob("2*.json"))) == 2  # the two run files
    summary = (out_dir / "results.md").read_text()
    assert "| checkout-slow | 2/2 |" in summary
    assert "| mechanism: connection pool | 2/2 |" in summary


def test_eval_rejects_unknown_scenario() -> None:
    result = CliRunner().invoke(cli.app, ["eval", "--scenario", "nope"])
    assert result.exit_code == 1
    assert "unknown scenario" in result.output
