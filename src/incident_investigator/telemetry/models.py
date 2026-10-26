"""Backend-neutral request and response models.

Every telemetry backend speaks these types, so the agent, tools, report and evals never see
anything backend-specific. Limits live here too, so every adapter inherits them.
"""

import re
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, Field, field_validator, model_validator

MAX_TIME_RANGE = timedelta(days=7)
MAX_LOG_LIMIT = 200
MAX_AGGREGATE_ROWS = 500


class Capability(StrEnum):
    LOGS = "logs"
    LOG_AGGREGATION = "log_aggregation"
    METRICS = "metrics"
    TOPOLOGY = "topology"
    CHANGES = "changes"
    KNOWN_ISSUES = "known_issues"


class CapabilityNotAvailable(Exception):
    """Raised by a backend asked for something it cannot provide."""

    def __init__(self, capability: Capability) -> None:
        super().__init__(f"'{capability}' is not available in this backend")
        self.capability = capability


class Level(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"


def as_utc(value: datetime) -> datetime:
    """Treat naive datetimes as UTC and convert aware ones to UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class TimeRange(BaseModel):
    """Half-open interval [start, end). Mandatory on every query and capped in length."""

    start: datetime
    end: datetime

    @field_validator("start", "end")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return as_utc(value)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.end <= self.start:
            raise ValueError("end must be after start")
        if self.end - self.start > MAX_TIME_RANGE:
            raise ValueError(f"time range longer than {MAX_TIME_RANGE.days} days; narrow it")
        return self

    def contains(self, ts: datetime) -> bool:
        return self.start <= ts < self.end


_TEMPLATE_PATTERNS = [
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "<uuid>"),
    (re.compile(r"\b(?:req|ord|usr|sku|pay)-[0-9a-z]+\b"), "<id>"),
    (re.compile(r"\b\d+(?:\.\d+)?(?:ms\b|s\b|MB\b|%|\b)"), "<n>"),
]


def message_template(message: str) -> str:
    """Collapse the variable parts of a log message so similar messages group together."""
    for pattern, replacement in _TEMPLATE_PATTERNS:
        message = pattern.sub(replacement, message)
    return message


# --- logs ---------------------------------------------------------------------------------


class LogRecord(BaseModel):
    timestamp: datetime
    service: str
    instance: str
    level: Level
    message: str
    attributes: dict[str, str | int | float | bool] = Field(default_factory=dict)
    request_id: str | None = None
    trace_id: str | None = None


class LogQuery(BaseModel):
    time_range: TimeRange
    services: list[str] = Field(default_factory=list, description="Empty means all services.")
    levels: list[Level] = Field(default_factory=list, description="Empty means all levels.")
    contains: str | None = Field(default=None, description="Case-insensitive substring.")
    request_id: str | None = None
    trace_id: str | None = None
    limit: int = Field(default=50, ge=1, le=MAX_LOG_LIMIT)


class LogPage(BaseModel):
    """Newest-first records matching a query. `truncated` means more matched than `limit`."""

    records: list[LogRecord]
    truncated: bool


class GroupField(StrEnum):
    SERVICE = "service"
    INSTANCE = "instance"
    LEVEL = "level"
    TEMPLATE = "template"


class LogAggregation(BaseModel):
    time_range: TimeRange
    group_by: list[GroupField] = Field(default_factory=lambda: [GroupField.SERVICE])
    services: list[str] = Field(default_factory=list)
    levels: list[Level] = Field(default_factory=list)
    contains: str | None = None
    bucket_minutes: int | None = Field(
        default=None, ge=1, le=24 * 60, description="Also group by time bucket of this size."
    )
    limit: int = Field(default=100, ge=1, le=MAX_AGGREGATE_ROWS)


class AggregateRow(BaseModel):
    bucket_start: datetime | None = None
    group: dict[str, str]
    count: int


class AggregateResult(BaseModel):
    """Rows ordered by bucket (if bucketed) then count descending."""

    rows: list[AggregateRow]
    truncated: bool


# --- metrics ------------------------------------------------------------------------------


class MetricQuery(BaseModel):
    time_range: TimeRange
    metric: str
    services: list[str] = Field(default_factory=list)
    step_minutes: int = Field(default=5, ge=1, le=24 * 60)
    per_instance: bool = Field(default=False, description="One series per instance.")


class MetricPoint(BaseModel):
    timestamp: datetime
    value: float


class MetricSeries(BaseModel):
    """One series per service (or instance), averaged over `step_minutes` buckets."""

    metric: str
    service: str
    instance: str | None = None
    unit: str
    points: list[MetricPoint]


# --- topology, changes, known issues ------------------------------------------------------


class Service(BaseModel):
    name: str
    description: str = ""
    instances: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)


class ServiceGraph(BaseModel):
    services: list[Service]


class ChangeKind(StrEnum):
    DEPLOY = "deploy"
    CONFIG = "config"
    RESTART = "restart"
    SCALE = "scale"


class ChangeEvent(BaseModel):
    timestamp: datetime
    service: str
    kind: ChangeKind
    description: str
    version: str | None = None
    actor: str | None = None


class KnownIssue(BaseModel):
    id: str
    title: str
    signature: str = Field(description="Text that appears in logs when this issue occurs.")
    services: list[str] = Field(default_factory=list)
    guidance: str


class MetricInfo(BaseModel):
    name: str
    unit: str
    description: str


class BackendInfo(BaseModel):
    name: str
    services: list[str]
    data_start: datetime
    data_end: datetime
    capabilities: list[Capability]
    metrics: list[MetricInfo] = Field(default_factory=list)
