"""SQLite telemetry backend, reading the synthetic demo dataset (see `demo/generate.py`)."""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path

from incident_investigator.telemetry.models import (
    AggregateResult,
    AggregateRow,
    BackendInfo,
    Capability,
    ChangeEvent,
    KnownIssue,
    LogAggregation,
    LogPage,
    LogQuery,
    LogRecord,
    MetricInfo,
    MetricPoint,
    MetricQuery,
    MetricSeries,
    Service,
    ServiceGraph,
    TimeRange,
)


def _ms(ts: datetime) -> int:
    return round(ts.timestamp() * 1000)


def _dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, UTC)


def _in(column: str, values: list[str]) -> tuple[str, list[str]]:
    return f"{column} IN ({', '.join('?' * len(values))})", list(values)


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class SQLiteBackend:
    """Read-only access to a demo database. Opens a connection per call, so it is thread-safe."""

    def __init__(self, path: Path) -> None:
        if not path.exists():
            raise FileNotFoundError(
                f"demo database not found at {path}; run `iinv generate-data` first"
            )
        self.path = path

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        with closing(con):
            yield con

    def describe(self) -> BackendInfo:
        with self._db() as db:
            meta = dict(db.execute("SELECT key, value FROM meta"))
            services = [name for (name,) in db.execute("SELECT name FROM services ORDER BY name")]
            metrics = [
                MetricInfo(name=n, unit=u, description=d)
                for n, u, d in db.execute("SELECT * FROM metric_defs ORDER BY name")
            ]
        return BackendInfo(
            name=f"sqlite: {meta['name']}",
            services=services,
            data_start=datetime.fromisoformat(meta["data_start"]),
            data_end=datetime.fromisoformat(meta["data_end"]),
            capabilities=list(Capability),
            metrics=metrics,
        )

    def _log_filters(
        self,
        time_range: TimeRange,
        services: list[str],
        levels: list[str],
        contains: str | None,
    ) -> tuple[list[str], list[object]]:
        where = ["ts >= ?", "ts < ?"]
        params: list[object] = [_ms(time_range.start), _ms(time_range.end)]
        for column, values in (("service", services), ("level", levels)):
            if values:
                clause, args = _in(column, values)
                where.append(clause)
                params += args
        if contains:
            where.append("message LIKE ? ESCAPE '\\'")
            params.append(_like(contains))
        return where, params

    def search_logs(self, query: LogQuery) -> LogPage:
        where, params = self._log_filters(
            query.time_range, query.services, query.levels, query.contains
        )
        for column, value in (("request_id", query.request_id), ("trace_id", query.trace_id)):
            if value:
                where.append(f"{column} = ?")
                params.append(value)
        sql = (
            "SELECT ts, service, instance, level, message, attributes, request_id, trace_id "
            f"FROM logs WHERE {' AND '.join(where)} ORDER BY ts DESC LIMIT ?"
        )
        with self._db() as db:
            rows = db.execute(sql, [*params, query.limit + 1]).fetchall()
        records = [
            LogRecord(
                timestamp=_dt(ts),
                service=service,
                instance=instance,
                level=level,
                message=message,
                attributes=json.loads(attributes),
                request_id=request_id,
                trace_id=trace_id,
            )
            for ts, service, instance, level, message, attributes, request_id, trace_id in rows
        ]
        return LogPage(records=records[: query.limit], truncated=len(records) > query.limit)

    def aggregate_logs(self, query: LogAggregation) -> AggregateResult:
        where, params = self._log_filters(
            query.time_range, query.services, query.levels, query.contains
        )
        columns = [field.value for field in query.group_by]
        select = list(columns)
        order = ["n DESC"]
        if query.bucket_minutes:
            width = query.bucket_minutes * 60_000
            select.insert(0, f"(ts / {width}) * {width} AS bucket")
            order.insert(0, "bucket")
        group = ["bucket", *columns] if query.bucket_minutes else columns
        sql = (
            f"SELECT {', '.join([*select, 'COUNT(*) AS n'])} FROM logs "
            f"WHERE {' AND '.join(where)} "
            f"{'GROUP BY ' + ', '.join(group) if group else ''} "
            f"ORDER BY {', '.join(order)} LIMIT ?"
        )
        with self._db() as db:
            rows = db.execute(sql, [*params, query.limit + 1]).fetchall()
        result = []
        for row in rows[: query.limit]:
            values = list(row)
            bucket = _dt(values.pop(0)) if query.bucket_minutes else None
            count = values.pop()
            result.append(
                AggregateRow(
                    bucket_start=bucket, group=dict(zip(columns, values, strict=True)), count=count
                )
            )
        return AggregateResult(rows=result, truncated=len(rows) > query.limit)

    def get_metrics(self, query: MetricQuery) -> list[MetricSeries]:
        with self._db() as db:
            unit_row = db.execute(
                "SELECT unit FROM metric_defs WHERE name = ?", [query.metric]
            ).fetchone()
            if unit_row is None:
                known = [n for (n,) in db.execute("SELECT name FROM metric_defs ORDER BY name")]
                raise ValueError(f"unknown metric '{query.metric}' (available: {', '.join(known)})")
            width = query.step_minutes * 60_000
            where = ["metric = ?", "ts >= ?", "ts < ?"]
            params: list[object] = [
                query.metric,
                _ms(query.time_range.start),
                _ms(query.time_range.end),
            ]
            if query.services:
                clause, args = _in("service", query.services)
                where.append(clause)
                params += args
            key = "service, instance" if query.per_instance else "service, NULL"
            rows = db.execute(
                f"SELECT {key}, (ts / {width}) * {width} AS bucket, AVG(value) FROM metrics "
                f"WHERE {' AND '.join(where)} GROUP BY 1, 2, bucket ORDER BY 1, 2, bucket",
                params,
            ).fetchall()
        series: dict[tuple[str, str | None], list[MetricPoint]] = {}
        for service, instance, bucket, value in rows:
            point = MetricPoint(timestamp=_dt(bucket), value=round(value, 2))
            series.setdefault((service, instance), []).append(point)
        return [
            MetricSeries(
                metric=query.metric, service=service, instance=instance, unit=unit_row[0], points=p
            )
            for (service, instance), p in series.items()
        ]

    def get_topology(self) -> ServiceGraph:
        with self._db() as db:
            rows = db.execute("SELECT * FROM services ORDER BY name").fetchall()
        return ServiceGraph(
            services=[
                Service(
                    name=name,
                    description=description,
                    instances=json.loads(instances),
                    depends_on=json.loads(depends_on),
                )
                for name, description, instances, depends_on in rows
            ]
        )

    def get_changes(
        self, time_range: TimeRange, services: list[str] | None = None
    ) -> list[ChangeEvent]:
        where = ["ts >= ?", "ts < ?"]
        params: list[object] = [_ms(time_range.start), _ms(time_range.end)]
        if services:
            clause, args = _in("service", services)
            where.append(clause)
            params += args
        with self._db() as db:
            rows = db.execute(
                f"SELECT * FROM changes WHERE {' AND '.join(where)} ORDER BY ts", params
            ).fetchall()
        return [
            ChangeEvent(
                timestamp=_dt(ts),
                service=service,
                kind=kind,
                description=description,
                version=version,
                actor=actor,
            )
            for ts, service, kind, description, version, actor in rows
        ]

    def known_issues(self) -> list[KnownIssue]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM known_issues ORDER BY id").fetchall()
        return [
            KnownIssue(
                id=id_, title=title, signature=signature, services=json.loads(services), guidance=g
            )
            for id_, title, signature, services, g in rows
        ]
