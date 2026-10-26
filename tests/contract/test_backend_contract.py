"""Contract every telemetry backend must satisfy, run against the synthetic demo data.

To check a new adapter: load the demo dataset into it and add a factory to `ADAPTERS`.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from incident_investigator.telemetry.base import TelemetryBackend
from incident_investigator.telemetry.models import (
    Capability,
    ChangeKind,
    GroupField,
    Level,
    LogAggregation,
    LogQuery,
    MetricQuery,
    TimeRange,
)
from incident_investigator.telemetry.sqlite import SQLiteBackend

ADAPTERS: dict[str, Callable[[Path], TelemetryBackend]] = {
    "sqlite": SQLiteBackend,
}

INCIDENT_DAY = datetime(2026, 3, 10, tzinfo=UTC)


def at(hh: int, mm: int = 0) -> datetime:
    return INCIDENT_DAY + timedelta(hours=hh, minutes=mm)


@pytest.fixture(params=sorted(ADAPTERS))
def backend(request: pytest.FixtureRequest, demo_db: Path) -> TelemetryBackend:
    return ADAPTERS[request.param](demo_db)


def test_implements_protocol(backend: TelemetryBackend) -> None:
    assert isinstance(backend, TelemetryBackend)


def test_describe(backend: TelemetryBackend) -> None:
    info = backend.describe()
    assert "payments" in info.services
    assert info.data_start < info.data_end
    assert set(info.capabilities) == set(Capability)
    assert "latency_p95_ms" in {m.name for m in info.metrics}


def test_search_logs_respects_range_order_and_limit(backend: TelemetryBackend) -> None:
    window = TimeRange(start=at(7), end=at(8))
    page = backend.search_logs(LogQuery(time_range=window, limit=20))
    assert len(page.records) == 20
    assert page.truncated
    assert all(window.contains(r.timestamp) for r in page.records)
    stamps = [r.timestamp for r in page.records]
    assert stamps == sorted(stamps, reverse=True)


def test_search_logs_filters(backend: TelemetryBackend) -> None:
    page = backend.search_logs(
        LogQuery(
            time_range=TimeRange(start=at(7), end=at(8)),
            services=["payments"],
            levels=[Level.ERROR],
            contains="ACQUIRING DB CONNECTION",
            limit=200,
        )
    )
    assert page.records
    for record in page.records:
        assert record.service == "payments"
        assert record.level is Level.ERROR
        assert "acquiring db connection" in record.message.lower()


def test_search_logs_by_request_id_links_services(backend: TelemetryBackend) -> None:
    window = TimeRange(start=at(7), end=at(8))
    failure = backend.search_logs(
        LogQuery(time_range=window, services=["payments"], levels=[Level.ERROR], limit=1)
    ).records[0]
    assert failure.request_id
    trail = backend.search_logs(LogQuery(time_range=window, request_id=failure.request_id))
    assert {r.service for r in trail.records} >= {"payments", "checkout"}


def test_search_logs_treats_wildcards_literally(backend: TelemetryBackend) -> None:
    page = backend.search_logs(
        LogQuery(time_range=TimeRange(start=at(7), end=at(8)), contains="%_%")
    )
    assert page.records == []


def test_aggregate_counts_match_search(backend: TelemetryBackend) -> None:
    window = TimeRange(start=at(6), end=at(7))
    rows = backend.aggregate_logs(
        LogAggregation(time_range=window, group_by=[GroupField.SERVICE], levels=[Level.ERROR])
    ).rows
    counts = {row.group["service"]: row.count for row in rows}
    payments = backend.search_logs(
        LogQuery(time_range=window, services=["payments"], levels=[Level.ERROR], limit=200)
    )
    assert not payments.truncated
    assert counts["payments"] == len(payments.records)
    assert [r.count for r in rows] == sorted((r.count for r in rows), reverse=True)


def test_aggregate_by_template_and_bucket(backend: TelemetryBackend) -> None:
    result = backend.aggregate_logs(
        LogAggregation(
            time_range=TimeRange(start=at(6), end=at(9)),
            group_by=[GroupField.TEMPLATE],
            services=["payments"],
            levels=[Level.ERROR],
            bucket_minutes=60,
        )
    )
    templates = {row.group["template"] for row in result.rows}
    assert "TimeoutError: acquiring db connection timed out after <n>" in templates
    buckets = [row.bucket_start for row in result.rows]
    assert buckets == sorted(buckets)
    assert {b.minute for b in buckets} == {0}


def test_aggregate_truncates(backend: TelemetryBackend) -> None:
    result = backend.aggregate_logs(
        LogAggregation(
            time_range=TimeRange(start=at(0), end=at(9)),
            group_by=[GroupField.TEMPLATE],
            limit=3,
        )
    )
    assert len(result.rows) == 3
    assert result.truncated


def test_metrics_series(backend: TelemetryBackend) -> None:
    window = TimeRange(start=at(5), end=at(9))
    series = backend.get_metrics(
        MetricQuery(
            time_range=window, metric="latency_p95_ms", services=["checkout"], step_minutes=30
        )
    )
    assert [(s.service, s.instance) for s in series] == [("checkout", None)]
    points = series[0].points
    assert len(points) == 8
    assert all(window.contains(p.timestamp) for p in points)
    before = points[0].value  # 05:00-05:30
    after = points[-1].value  # 08:30-09:00
    assert after > 5 * before


def test_metrics_per_instance(backend: TelemetryBackend) -> None:
    series = backend.get_metrics(
        MetricQuery(
            time_range=TimeRange(start=at(0), end=at(1)),
            metric="memory_mb",
            services=["inventory"],
            per_instance=True,
        )
    )
    assert {s.instance for s in series} == {"inventory-1", "inventory-2"}


def test_unknown_metric_is_a_value_error(backend: TelemetryBackend) -> None:
    with pytest.raises(ValueError, match="available:"):
        backend.get_metrics(
            MetricQuery(time_range=TimeRange(start=at(0), end=at(1)), metric="disk_iops")
        )


def test_topology_is_consistent(backend: TelemetryBackend) -> None:
    graph = backend.get_topology()
    names = {s.name for s in graph.services}
    assert "checkout" in names
    for service in graph.services:
        assert service.instances
        assert set(service.depends_on) <= names


def test_changes(backend: TelemetryBackend) -> None:
    window = TimeRange(start=at(0), end=at(9))
    changes = backend.get_changes(window)
    assert [c.timestamp for c in changes] == sorted(c.timestamp for c in changes)
    assert all(window.contains(c.timestamp) for c in changes)
    deploys = [c for c in changes if c.kind is ChangeKind.DEPLOY]
    assert [(c.service, c.version) for c in deploys] == [("payments", "2.14.0")]
    assert {c.service for c in backend.get_changes(window, ["checkout"])} == {"checkout"}


def test_known_issues(backend: TelemetryBackend) -> None:
    issues = backend.known_issues()
    assert issues
    assert all(issue.signature and issue.guidance for issue in issues)
