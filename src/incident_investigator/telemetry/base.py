"""The pluggability seam: the interface every telemetry backend implements."""

from typing import Protocol, runtime_checkable

from incident_investigator.telemetry.models import (
    AggregateResult,
    BackendInfo,
    ChangeEvent,
    KnownIssue,
    LogAggregation,
    LogPage,
    LogQuery,
    MetricQuery,
    MetricSeries,
    ServiceGraph,
    TimeRange,
)


@runtime_checkable
class TelemetryBackend(Protocol):
    """Read-only access to logs, metrics, topology, changes and known issues.

    Methods for a capability missing from `describe().capabilities` raise
    `CapabilityNotAvailable`; invalid requests (e.g. an unknown metric) raise `ValueError`
    with a message the agent can act on. All timestamps are UTC.
    """

    def describe(self) -> BackendInfo: ...

    def search_logs(self, query: LogQuery) -> LogPage: ...

    def aggregate_logs(self, query: LogAggregation) -> AggregateResult: ...

    def get_metrics(self, query: MetricQuery) -> list[MetricSeries]: ...

    def get_topology(self) -> ServiceGraph: ...

    def get_changes(
        self, time_range: TimeRange, services: list[str] | None = None
    ) -> list[ChangeEvent]: ...

    def known_issues(self) -> list[KnownIssue]: ...
