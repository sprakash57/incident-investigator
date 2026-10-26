from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from incident_investigator.config import Settings
from incident_investigator.telemetry.models import (
    MAX_LOG_LIMIT,
    MAX_TIME_RANGE,
    LogQuery,
    TimeRange,
    message_template,
)
from incident_investigator.telemetry.registry import UnknownBackendError, get_backend

T0 = datetime(2026, 3, 10, 6, 0, tzinfo=UTC)


def test_time_range_normalises_to_utc() -> None:
    naive = TimeRange(start=datetime(2026, 3, 10, 6, 0), end=datetime(2026, 3, 10, 7, 0))
    assert naive.start == T0
    plus_one = timezone(timedelta(hours=1))
    aware = TimeRange(start=datetime(2026, 3, 10, 7, 0, tzinfo=plus_one), end=T0 + timedelta(1))
    assert aware.start == T0


def test_time_range_rejects_inverted() -> None:
    with pytest.raises(ValidationError, match="end must be after start"):
        TimeRange(start=T0, end=T0)


def test_time_range_rejects_over_cap() -> None:
    with pytest.raises(ValidationError, match="narrow it"):
        TimeRange(start=T0, end=T0 + MAX_TIME_RANGE + timedelta(seconds=1))


def test_log_query_requires_time_range_and_caps_limit() -> None:
    with pytest.raises(ValidationError):
        LogQuery()  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        LogQuery(time_range=TimeRange(start=T0, end=T0 + timedelta(1)), limit=MAX_LOG_LIMIT + 1)


@pytest.mark.parametrize(
    ("message", "template"),
    [
        ("Request req-8f3a2 took 2350ms", "Request <id> took <n>"),
        (
            "pool exhausted: 10/10 in use, waited 5000 ms",
            "pool exhausted: <n>/<n> in use, waited <n> ms",
        ),
        ("order 3f2b8c1e-1d2a-4c3b-9e8f-0a1b2c3d4e5f created", "order <uuid> created"),
        ("heap usage 87%", "heap usage <n>"),
    ],
)
def test_message_template(message: str, template: str) -> None:
    assert message_template(message) == template


def test_unknown_backend_lists_available() -> None:
    with pytest.raises(UnknownBackendError, match="unknown backend 'nope'"):
        get_backend(Settings(backend="nope"))
