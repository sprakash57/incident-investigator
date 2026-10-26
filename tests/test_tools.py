import ast
import inspect
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic_ai import ModelRetry

from incident_investigator.agent import tools
from incident_investigator.agent.deps import InvestigationDeps
from incident_investigator.agent.trace import PlanRecord, ToolCallRecord, Trace
from incident_investigator.telemetry.models import Capability, CapabilityNotAvailable, Level
from incident_investigator.telemetry.sqlite import SQLiteBackend

NOW = datetime(2026, 3, 10, 9, 0, tzinfo=UTC)
START = datetime(2026, 3, 10, 6, 0, tzinfo=UTC)


def make_ctx(backend: Any, listener: Any = None) -> Any:
    return SimpleNamespace(deps=InvestigationDeps(backend=backend, now=NOW, trace=Trace(listener)))


@pytest.fixture
def ctx(demo_db: Path) -> Any:
    return make_ctx(SQLiteBackend(demo_db))


def test_describe_environment(ctx: Any) -> None:
    out = tools.describe_environment(ctx)
    assert out["call_id"] == "t1"
    assert out["now"] == "2026-03-10T09:00:00Z"
    assert "payments" in out["services"]


def test_search_logs_returns_compact_lines_and_records_call(ctx: Any) -> None:
    out = tools.search_logs(ctx, START, NOW, services=["payments"], levels=[Level.ERROR], limit=3)
    assert len(out["lines"]) == 3
    assert out["truncated"] is True
    assert "payments-" in out["lines"][0]
    assert "[request_id=req-" in out["lines"][0]
    (call,) = ctx.deps.trace.calls
    assert call.id == out["call_id"]
    assert call.args == {
        "start": START,
        "end": NOW,
        "services": ["payments"],
        "levels": [Level.ERROR],
        "limit": 3,
    }
    assert call.latency_ms is not None


def test_aggregate_logs_by_template(ctx: Any) -> None:
    out = tools.aggregate_logs(
        ctx, START, NOW, group_by=["template"], levels=[Level.ERROR], bucket_minutes=60
    )
    first = out["rows"][0]
    assert set(first) == {"bucket", "template", "count"}
    assert any("acquiring db connection timed out" in row["template"] for row in out["rows"])


def test_get_metrics(ctx: Any) -> None:
    out = tools.get_metrics(
        ctx, "latency_p95_ms", START, NOW, services=["checkout"], step_minutes=60
    )
    (series,) = out["series"]
    assert series["unit"] == "ms"
    assert len(series["points"]) == 3


def test_too_many_metric_points_asks_model_to_retry(ctx: Any) -> None:
    with pytest.raises(ModelRetry, match="larger step_minutes"):
        tools.get_metrics(ctx, "cpu_pct", START, NOW, step_minutes=1, per_instance=True)
    assert ctx.deps.trace.calls[0].error


def test_invalid_time_range_asks_model_to_retry(ctx: Any) -> None:
    with pytest.raises(ModelRetry, match="end must be after start"):
        tools.get_changes(ctx, NOW, START)


def test_unknown_metric_lists_available(ctx: Any) -> None:
    with pytest.raises(ModelRetry, match="available: "):
        tools.get_metrics(ctx, "disk_iops", START, NOW)


def test_changes_topology_known_issues(ctx: Any) -> None:
    changes = tools.get_changes(ctx, START, NOW, services=["payments"])["changes"]
    assert changes == [
        {
            "time": "2026-03-10T06:22:14Z",
            "service": "payments",
            "kind": "deploy",
            "description": "payments v2.14.0: upgrade postgres driver, refactor config loading",
            "version": "2.14.0",
            "actor": "ci-deploy",
        }
    ]
    topology = tools.get_topology(ctx)["services"]
    assert "payments" in next(s for s in topology if s["name"] == "checkout")["calls"]
    assert tools.known_issues(ctx)["known_issues"]
    assert [c.id for c in ctx.deps.trace.calls] == ["t1", "t2", "t3"]


class BrokenBackend:
    def get_topology(self) -> None:
        raise CapabilityNotAvailable(Capability.TOPOLOGY)

    def known_issues(self) -> None:
        raise ConnectionError("backend unreachable")


def test_missing_capability_is_a_result_not_an_error() -> None:
    out = tools.get_topology(make_ctx(BrokenBackend()))
    assert out == {"call_id": "t1", "unavailable": "'topology' is not available in this backend"}


def test_backend_failure_keeps_run_alive() -> None:
    with pytest.raises(ModelRetry, match="known_issues failed: ConnectionError"):
        tools.known_issues(make_ctx(BrokenBackend()))


def test_listener_sees_plan_and_finished_calls(demo_db: Path) -> None:
    seen: list[ToolCallRecord | PlanRecord] = []
    ctx = make_ctx(SQLiteBackend(demo_db), seen.append)
    tools.record_plan(ctx, ["check changes", "check errors"])
    tools.get_topology(ctx)
    plan, call = seen
    assert isinstance(plan, PlanRecord)
    assert plan.steps == ["check changes", "check errors"]
    assert isinstance(call, ToolCallRecord)
    assert call.result is not None


def test_tools_do_not_depend_on_a_specific_backend() -> None:
    tree = ast.parse(inspect.getsource(tools))
    imported = {
        node.module if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }
    assert not {m for m in imported if m and ("sqlite" in m or "boto" in m)}
