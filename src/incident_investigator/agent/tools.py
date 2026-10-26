"""Agent tools: thin, backend-agnostic wrappers that turn backend models into compact JSON.

Every call is recorded in the run's `Trace` under a short id (`t1`, `t2`, ...) that is returned
with the result, so the final report can cite exactly which call produced each piece of
evidence. Bad arguments come back to the model as a retry prompt; a capability the backend
lacks comes back as a plain "unavailable" result. Nothing here knows which backend is in use.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from pydantic_ai import ModelRetry, RunContext

from incident_investigator.agent.deps import InvestigationDeps
from incident_investigator.telemetry.models import (
    CapabilityNotAvailable,
    GroupField,
    Level,
    LogAggregation,
    LogQuery,
    LogRecord,
    MetricQuery,
    TimeRange,
)

Ctx = RunContext[InvestigationDeps]

MAX_METRIC_POINTS = 400


def _iso(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def _log_line(record: LogRecord) -> str:
    line = f"{_iso(record.timestamp)} {record.instance} {record.level} {record.message}"
    if record.request_id:
        line += f" [request_id={record.request_id}]"
    return line


def _call(ctx: Ctx, tool: str, args: dict[str, Any], fn: Callable[[], Any]) -> dict[str, Any]:
    """Run `fn`, record it in the trace, and shape the outcome for the model."""
    trace = ctx.deps.trace
    record = trace.start(tool, {k: v for k, v in args.items() if v not in (None, [], "")})
    retry: str | None = None
    try:
        record.result = fn()
    except CapabilityNotAvailable as exc:
        record.result = {"unavailable": str(exc)}
    except ValueError as exc:  # includes pydantic ValidationError
        record.error = _error_text(exc)
        retry = f"{tool}: {record.error}"
    except Exception as exc:  # backend failure: tell the model, keep the run alive
        record.error = f"{type(exc).__name__}: {exc}"
        retry = f"{tool} failed: {record.error}. Try a narrower query."
    trace.finish(record)
    if retry:
        raise ModelRetry(retry)
    return {"call_id": record.id, **record.result}


def _error_text(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(
            f"{'.'.join(map(str, e['loc'])) or 'request'}: {e['msg']}" for e in exc.errors()
        )
    return str(exc)


def record_plan(ctx: Ctx, steps: list[str]) -> str:
    """Record your investigation plan before using any other tool.

    Args:
        steps: 3-6 short steps, each naming what you will check and why.
    """
    ctx.deps.trace.add_plan(steps)
    return "Plan recorded. Follow it, and adapt it if the evidence points elsewhere."


def describe_environment(ctx: Ctx) -> dict[str, Any]:
    """Services, the time span covered by the data, the current time, and available metrics."""

    def run() -> dict[str, Any]:
        info = ctx.deps.backend.describe()
        return {
            "backend": info.name,
            "now": _iso(ctx.deps.now),
            "data_start": _iso(info.data_start),
            "data_end": _iso(info.data_end),
            "services": info.services,
            "capabilities": [c.value for c in info.capabilities],
            "metrics": [f"{m.name} ({m.unit}): {m.description}" for m in info.metrics],
        }

    return _call(ctx, "describe_environment", {}, run)


def search_logs(
    ctx: Ctx,
    start: datetime,
    end: datetime,
    services: list[str] | None = None,
    levels: list[Level] | None = None,
    contains: str | None = None,
    request_id: str | None = None,
    limit: int = 30,
) -> dict[str, Any]:
    """Fetch individual log lines, newest first.

    Use to read concrete messages once you know roughly where to look; use `aggregate_logs`
    first to find where errors are concentrated.

    Args:
        start: Start of the window (UTC, inclusive).
        end: End of the window (UTC, exclusive). At most 7 days after start.
        services: Only these services. Omit for all.
        levels: Only these levels (DEBUG, INFO, WARN, ERROR). Omit for all.
        contains: Case-insensitive text the message must contain.
        request_id: Follow one request across services.
        limit: Maximum lines to return (1-200).
    """
    args = {k: v for k, v in locals().items() if k != "ctx"}

    def run() -> dict[str, Any]:
        page = ctx.deps.backend.search_logs(
            LogQuery(
                time_range=TimeRange(start=start, end=end),
                services=services or [],
                levels=levels or [],
                contains=contains,
                request_id=request_id,
                limit=limit,
            )
        )
        return {"lines": [_log_line(r) for r in page.records], "truncated": page.truncated}

    return _call(ctx, "search_logs", args, run)


def aggregate_logs(
    ctx: Ctx,
    start: datetime,
    end: datetime,
    group_by: list[GroupField] | None = None,
    services: list[str] | None = None,
    levels: list[Level] | None = None,
    contains: str | None = None,
    bucket_minutes: int | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Count log lines grouped by service, instance, level and/or message template.

    `template` groups messages with their variable parts (ids, numbers) replaced, which is the
    fastest way to see *which* errors are happening. Add `bucket_minutes` to see when.

    Args:
        start: Start of the window (UTC, inclusive).
        end: End of the window (UTC, exclusive). At most 7 days after start.
        group_by: Any of service, instance, level, template. Defaults to service.
        services: Only these services. Omit for all.
        levels: Only these levels. Omit for all.
        contains: Case-insensitive text the message must contain.
        bucket_minutes: Also group by time buckets of this many minutes.
        limit: Maximum rows to return (1-500).
    """
    args = {k: v for k, v in locals().items() if k != "ctx"}

    def run() -> dict[str, Any]:
        result = ctx.deps.backend.aggregate_logs(
            LogAggregation(
                time_range=TimeRange(start=start, end=end),
                group_by=group_by or [GroupField.SERVICE],
                services=services or [],
                levels=levels or [],
                contains=contains,
                bucket_minutes=bucket_minutes,
                limit=limit,
            )
        )
        rows = []
        for row in result.rows:
            item: dict[str, Any] = dict(row.group)
            if row.bucket_start:
                item = {"bucket": _iso(row.bucket_start), **item}
            rows.append(item | {"count": row.count})
        return {"rows": rows, "truncated": result.truncated}

    return _call(ctx, "aggregate_logs", args, run)


def get_metrics(
    ctx: Ctx,
    metric: str,
    start: datetime,
    end: datetime,
    services: list[str] | None = None,
    step_minutes: int = 15,
    per_instance: bool = False,
) -> dict[str, Any]:
    """Time series for one metric, averaged per step, one series per service (or instance).

    Call `describe_environment` for the metric names. Compare a window before the incident
    with the incident window to quantify a change.

    Args:
        metric: Metric name, e.g. latency_p95_ms.
        start: Start of the window (UTC, inclusive).
        end: End of the window (UTC, exclusive). At most 7 days after start.
        services: Only these services. Omit for all.
        step_minutes: Bucket size in minutes; use larger steps for longer windows.
        per_instance: One series per instance instead of per service.
    """
    args = {k: v for k, v in locals().items() if k != "ctx"}

    def run() -> dict[str, Any]:
        series = ctx.deps.backend.get_metrics(
            MetricQuery(
                time_range=TimeRange(start=start, end=end),
                metric=metric,
                services=services or [],
                step_minutes=step_minutes,
                per_instance=per_instance,
            )
        )
        total = sum(len(s.points) for s in series)
        if total > MAX_METRIC_POINTS:
            raise ValueError(
                f"{total} points requested (max {MAX_METRIC_POINTS}); "
                "use a larger step_minutes, fewer services or a shorter window"
            )
        return {
            "series": [
                {
                    "service": s.service,
                    **({"instance": s.instance} if s.instance else {}),
                    "unit": s.unit,
                    "points": [[_iso(p.timestamp), p.value] for p in s.points],
                }
                for s in series
            ]
        }

    return _call(ctx, "get_metrics", args, run)


def get_topology(ctx: Ctx) -> dict[str, Any]:
    """Services, their instances, and which services each one calls (its dependencies)."""

    def run() -> dict[str, Any]:
        graph = ctx.deps.backend.get_topology()
        return {
            "services": [
                {
                    "name": s.name,
                    "description": s.description,
                    "instances": s.instances,
                    "calls": s.depends_on,
                }
                for s in graph.services
            ]
        }

    return _call(ctx, "get_topology", {}, run)


def get_changes(
    ctx: Ctx, start: datetime, end: datetime, services: list[str] | None = None
) -> dict[str, Any]:
    """Deploys, config changes, restarts and scaling events, oldest first.

    "What changed?" is often the fastest route to a root cause: look at changes shortly
    before the symptoms began, in the affected service and in what it depends on.

    Args:
        start: Start of the window (UTC, inclusive).
        end: End of the window (UTC, exclusive). At most 7 days after start.
        services: Only these services. Omit for all.
    """
    args = {k: v for k, v in locals().items() if k != "ctx"}

    def run() -> dict[str, Any]:
        changes = ctx.deps.backend.get_changes(TimeRange(start=start, end=end), services)
        return {
            "changes": [
                {
                    "time": _iso(c.timestamp),
                    "service": c.service,
                    "kind": c.kind.value,
                    "description": c.description,
                    **({"version": c.version} if c.version else {}),
                    **({"actor": c.actor} if c.actor else {}),
                }
                for c in changes
            ]
        }

    return _call(ctx, "get_changes", args, run)


def known_issues(ctx: Ctx) -> dict[str, Any]:
    """Runbook entries for known problems: the log signature to look for and what to do."""

    def run() -> dict[str, Any]:
        return {"known_issues": [k.model_dump() for k in ctx.deps.backend.known_issues()]}

    return _call(ctx, "known_issues", {}, run)


TOOLS = [
    record_plan,
    describe_environment,
    search_logs,
    aggregate_logs,
    get_metrics,
    get_topology,
    get_changes,
    known_issues,
]
