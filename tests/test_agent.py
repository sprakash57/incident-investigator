from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from incident_investigator.agent.deps import InvestigationDeps
from incident_investigator.agent.investigator import investigate
from incident_investigator.agent.report import IncidentReport
from incident_investigator.telemetry.sqlite import SQLiteBackend

NOW = datetime(2026, 3, 10, 9, 0, tzinfo=UTC)


def report_args(cite: str) -> dict[str, Any]:
    return {
        "summary": "payments pool exhausted",
        "root_cause_hypothesis": "payments v2.14.0 shrank the DB pool",
        "confidence": {"level": "high", "rationale": "deploy + pool timeouts line up"},
        "evidence": [{"claim": "pool timeouts", "source_call_ids": [cite], "excerpt": "..."}],
        "timeline": [{"time": "2026-03-10T06:22:14Z", "event": "deploy", "source_call_ids": []}],
        "affected_services": ["payments", "checkout"],
        "blast_radius": "checkout payments failing",
        "recommended_actions": ["roll back payments to 2.13.2"],
    }


@pytest.fixture
def deps(demo_db: Path) -> InvestigationDeps:
    return InvestigationDeps(backend=SQLiteBackend(demo_db), now=NOW)


def test_runs_tools_and_returns_report(deps: InvestigationDeps) -> None:
    model = TestModel(
        call_tools=["describe_environment"],
        custom_output_args=report_args(cite="t1"),
    )
    result = investigate("checkout is slow", deps, model=model, max_tool_calls=10)
    assert isinstance(result.report, IncidentReport)
    assert [c.tool for c in deps.trace.calls] == ["describe_environment"]
    assert result.usage.tool_calls == 1


def scripted(*steps: Any) -> FunctionModel:
    """A model that replays one scripted response per request and records what it saw."""
    seen: list[list[ModelMessage]] = []
    remaining = list(steps)

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(messages)
        tool, args = remaining.pop(0)
        name = info.output_tools[0].name if tool == "OUTPUT" else tool
        return ModelResponse(parts=[ToolCallPart(name, args)])

    model = FunctionModel(respond)
    model.seen = seen  # type: ignore[attr-defined]
    return model


def returns_to_model(messages: list[ModelMessage]) -> list[ToolReturnPart]:
    return [
        part
        for message in messages
        for part in getattr(message, "parts", [])
        if isinstance(part, ToolReturnPart)
    ]


def test_bad_tool_args_and_fake_citations_go_back_to_the_model(deps: InvestigationDeps) -> None:
    model = scripted(
        ("get_changes", {"start": "2026-03-10T09:00:00Z", "end": "2026-03-10T06:00:00Z"}),
        ("get_changes", {"start": "2026-03-10T06:00:00Z", "end": "2026-03-10T09:00:00Z"}),
        ("OUTPUT", report_args(cite="t99")),
        ("OUTPUT", report_args(cite="t2")),
    )
    result = investigate("checkout is slow", deps, model=model, max_tool_calls=10)

    assert result.report.evidence[0].source_call_ids == ["t2"]
    first, second = deps.trace.calls
    assert first.error and "end must be after start" in first.error
    assert second.error is None
    assert second.result["changes"]

    seen = model.seen  # type: ignore[attr-defined]
    retry = returns_to_model(seen[1])[-1]
    assert retry.outcome == "retried"
    assert "end must be after start" in str(retry.content)
    citation_retry = returns_to_model(seen[3])[-1]
    assert "t99" in str(citation_retry.content)


def test_tool_call_budget_is_enforced(deps: InvestigationDeps) -> None:
    from pydantic_ai.exceptions import UsageLimitExceeded

    model = scripted(*[("get_topology", {})] * 5)
    with pytest.raises(UsageLimitExceeded):
        investigate("checkout is slow", deps, model=model, max_tool_calls=2)


def test_model_sees_its_remaining_budget(deps: InvestigationDeps) -> None:
    model = scripted(
        ("get_topology", {}),
        ("known_issues", {}),
        ("OUTPUT", report_args(cite="t1")),
    )
    investigate("checkout is slow", deps, model=model, max_tool_calls=4)
    remaining = [
        part.content["tool_calls_remaining"]
        for part in returns_to_model(model.seen[-1])  # type: ignore[attr-defined]
        if isinstance(part.content, dict)
    ]
    assert remaining == [3, 2]
