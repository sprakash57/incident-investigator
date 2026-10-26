"""Seeded generator for the "Acme Commerce" synthetic telemetry dataset.

Two days of background logs and metrics for a fictional e-commerce platform, with change
events and planted incidents whose ground truth is stored alongside (in a table that
backends never expose to the agent). Same seed + same generator version = same database.

Planted incidents:

1. payments v2.14.0 (deployed 2026-03-10 06:22) shrinks the DB connection pool from 20 to 5.
   Harmless at night-time traffic; as the morning ramp passes the pool's capacity (~06:30),
   payments requests queue for connections, time out, and checkout latency and errors climb.
2. inventory v3.8.0 (deployed 2026-03-08 13:00) leaks memory through an unbounded cache;
   instances are OOM-killed and restarted until v3.8.1 bounds the cache.
"""

import json
import math
import random
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from incident_investigator.telemetry.models import message_template

GENERATOR_VERSION = "1"
DEFAULT_SEED = 42

DATA_START = datetime(2026, 3, 8, 9, 0, tzinfo=UTC)
DATA_END = datetime(2026, 3, 10, 9, 0, tzinfo=UTC)
METRIC_STEP = timedelta(minutes=5)

PAYMENTS_DEPLOY = datetime(2026, 3, 10, 6, 22, 14, tzinfo=UTC)
INVENTORY_LEAK_DEPLOY = datetime(2026, 3, 8, 13, 0, 5, tzinfo=UTC)
INVENTORY_FIX_DEPLOY = datetime(2026, 3, 9, 15, 30, 40, tzinfo=UTC)
# Fraction of peak traffic that a 5-connection payments pool can serve.
SMALL_POOL_CAPACITY = 0.39


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    description: str
    depends_on: tuple[str, ...]
    logs_per_min: float  # per instance, at peak traffic
    latency_ms: float  # p95 at normal load
    memory_mb: float
    instances: int = 2

    @property
    def instance_names(self) -> list[str]:
        return [f"{self.name}-{i}" for i in range(1, self.instances + 1)]


SERVICES = [
    ServiceSpec(
        "gateway",
        "Public API gateway; routes web and mobile traffic.",
        ("auth", "catalog", "checkout", "orders"),
        1.2,
        90,
        310,
    ),
    ServiceSpec("auth", "Login, sessions and token issuance.", (), 0.6, 45, 260),
    ServiceSpec("catalog", "Product listings and search.", ("inventory",), 0.8, 110, 520),
    ServiceSpec(
        "checkout",
        "Cart checkout orchestration.",
        ("payments", "inventory", "orders"),
        0.8,
        260,
        380,
    ),
    ServiceSpec(
        "payments",
        "Card payment authorization via an external payment provider; Postgres-backed.",
        (),
        0.6,
        120,
        340,
    ),
    ServiceSpec("orders", "Order records and status.", ("notifications",), 0.6, 70, 300),
    ServiceSpec("inventory", "Stock levels and reservations.", (), 0.6, 60, 420),
    ServiceSpec("notifications", "Order and account emails.", (), 0.3, 200, 210, instances=1),
]
SERVICE_BY_NAME = {s.name: s for s in SERVICES}

METRIC_DEFS = [
    ("latency_p95_ms", "ms", "95th percentile request latency"),
    ("error_rate_pct", "%", "Share of requests returning 5xx"),
    ("request_rate", "req/min", "Requests handled per minute"),
    ("cpu_pct", "%", "Container CPU utilisation"),
    ("memory_mb", "MB", "Container memory in use"),
]

KNOWN_ISSUES = [
    {
        "id": "KI-101",
        "title": "Database connection pool timeouts",
        "signature": "acquiring db connection timed out",
        "services": ["payments", "orders"],
        "guidance": (
            "Requests are waiting for a free DB connection. Check whether pool size or DB "
            "config changed recently (deploys, config pushes), compare traffic against pool "
            "capacity, and look for slow queries holding connections."
        ),
    },
    {
        "id": "KI-102",
        "title": "Node.js heap exhaustion",
        "signature": "JavaScript heap out of memory",
        "services": [],
        "guidance": (
            "Process exceeded its heap limit and was killed. Look for steadily growing memory "
            "after a deploy (leak) versus a sudden spike (large payload). Restarts hide the "
            "symptom; roll back or fix the leak."
        ),
    },
    {
        "id": "KI-103",
        "title": "SMTP provider throttling",
        "signature": "421 try again later",
        "services": ["notifications"],
        "guidance": (
            "The email provider is rate limiting. Messages are retried automatically; only "
            "act if the retry queue keeps growing for more than an hour."
        ),
    },
]

GROUND_TRUTH = [
    {
        "id": "payments-pool-exhaustion",
        "title": "Checkout slow and failing: payments DB pool exhaustion after v2.14.0",
        "start": "2026-03-10T06:30:00+00:00",
        "end": None,
        "root_cause": (
            "payments v2.14.0, deployed 2026-03-10 06:22 UTC, reduced the database connection "
            "pool from 20 to 5 connections (startup log 'max_connections=5'). When morning "
            "traffic ramped past the small pool's capacity around 06:30, payments requests "
            "queued for connections and timed out, making checkout slow and causing payment "
            "authorization failures."
        ),
        "affected_services": ["payments", "checkout", "gateway"],
        "key_evidence": [
            "deploy payments v2.14.0 at 06:22",
            "payments startup log max_connections=5 (was 20)",
            "payments 'acquiring db connection timed out' errors from ~06:30",
            "checkout and payments p95 latency rising from ~06:30",
            "payments CPU normal (not a capacity/CPU problem)",
        ],
        "red_herrings": ["checkout feature flag change at 05:50"],
    },
    {
        "id": "inventory-memory-leak",
        "title": "inventory OOM restarts after v3.8.0",
        "start": "2026-03-08T13:00:00+00:00",
        "end": "2026-03-09T15:30:40+00:00",
        "root_cause": (
            "inventory v3.8.0 introduced an unbounded product cache; memory grew steadily "
            "until instances hit the Node.js heap limit and were OOM-killed and restarted by "
            "ECS. v3.8.1 bounded the cache and memory stabilised."
        ),
        "affected_services": ["inventory", "checkout", "catalog"],
        "key_evidence": [
            "deploy inventory v3.8.0 at 2026-03-08 13:00",
            "inventory memory_mb climbing per instance",
            "'JavaScript heap out of memory' fatal errors and ECS restarts",
            "memory flat after v3.8.1 at 2026-03-09 15:30",
        ],
        "red_herrings": [],
    },
]


@dataclass
class Dataset:
    logs: list[tuple] = field(default_factory=list)
    metrics: list[tuple] = field(default_factory=list)
    changes: list[tuple] = field(default_factory=list)


def traffic(ts: datetime) -> float:
    """Fraction of peak traffic: a quiet night and a daytime bump peaking mid-afternoon UTC."""
    hour = ts.hour + ts.minute / 60 + ts.second / 3600
    if 5 < hour < 23:
        return 0.2 + 0.8 * math.sin(math.pi * (hour - 5) / 18)
    return 0.2


def payments_saturation(ts: datetime) -> float:
    """>1 when the 5-connection pool cannot keep up with traffic; 0 before the bad deploy."""
    if ts < PAYMENTS_DEPLOY:
        return 0.0
    return traffic(ts) / SMALL_POOL_CAPACITY


HEAP_LIMIT_MB = 1024
LEAK_MB_PER_HOUR = {"inventory-1": 41.0, "inventory-2": 62.0}


def _oom_restarts(instance: str) -> list[datetime]:
    """Times the leaking instance reaches the heap limit and is killed, until the fix ships."""
    headroom = HEAP_LIMIT_MB - SERVICE_BY_NAME["inventory"].memory_mb
    lifetime = timedelta(hours=headroom / LEAK_MB_PER_HOUR[instance])
    restarts = []
    ts = INVENTORY_LEAK_DEPLOY + lifetime
    while ts < INVENTORY_FIX_DEPLOY:
        restarts.append(ts.replace(microsecond=0))
        ts += lifetime
    return restarts


OOM_RESTARTS = {instance: _oom_restarts(instance) for instance in LEAK_MB_PER_HOUR}


def inventory_memory(ts: datetime, instance: str) -> float:
    """Memory (MB) for an inventory instance, including the v3.8.0 leak and OOM restarts."""
    base = SERVICE_BY_NAME["inventory"].memory_mb
    if not INVENTORY_LEAK_DEPLOY <= ts < INVENTORY_FIX_DEPLOY:
        return base
    since = max([INVENTORY_LEAK_DEPLOY, *(r for r in OOM_RESTARTS[instance] if r <= ts)])
    return base + LEAK_MB_PER_HOUR[instance] * (ts - since).total_seconds() / 3600


class Generator:
    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.data = Dataset()

    # --- helpers --------------------------------------------------------------------------

    def _id(self, prefix: str) -> str:
        return f"{prefix}-{self.rng.getrandbits(32):08x}"

    def _ms(self, base: float, spread: float = 0.25) -> int:
        return max(1, round(self.rng.gauss(base, base * spread)))

    def log(
        self,
        ts: datetime,
        instance: str,
        level: str,
        message: str,
        request_id: str | None = None,
        **attributes: str | int | float | bool,
    ) -> None:
        service = instance.rsplit("-", 1)[0]
        self.data.logs.append(
            (
                _epoch_ms(ts),
                service,
                instance,
                level,
                message,
                message_template(message),
                json.dumps(attributes, sort_keys=True),
                request_id,
                None,
            )
        )

    def change(
        self,
        ts: datetime,
        service: str,
        kind: str,
        description: str,
        version: str | None = None,
        actor: str | None = None,
    ) -> None:
        self.data.changes.append((_epoch_ms(ts), service, kind, description, version, actor))

    def minutes(self) -> Iterator[datetime]:
        ts = DATA_START
        while ts < DATA_END:
            yield ts
            ts += timedelta(minutes=1)

    def jitter(self, minute: datetime) -> datetime:
        return minute + timedelta(seconds=self.rng.uniform(0, 60))

    def count(self, mean: float) -> int:
        """Small Poisson-ish count without numpy."""
        whole = int(mean)
        return whole + (1 if self.rng.random() < mean - whole else 0)

    # --- generation -----------------------------------------------------------------------

    def run(self) -> Dataset:
        self.changes_and_startups()
        for minute in self.minutes():
            self.background_minute(minute)
            self.payments_incident_minute(minute)
            self.inventory_incident_minute(minute)
        self.metrics()
        self.data.logs.sort()
        self.data.changes.sort()
        return self.data

    def deploy(
        self,
        ts: datetime,
        service: str,
        version: str,
        description: str,
        startup_extra: str | None = None,
    ) -> None:
        self.change(ts, service, "deploy", description, version, "ci-deploy")
        for i, instance in enumerate(SERVICE_BY_NAME[service].instance_names):
            started = ts + timedelta(seconds=40 + 35 * i)
            self.log(started, instance, "INFO", f"service starting version={version}")
            if startup_extra:
                self.log(started + timedelta(seconds=2), instance, "INFO", startup_extra)
            self.log(started + timedelta(seconds=4), instance, "INFO", "health check passing")

    def changes_and_startups(self) -> None:
        day1, day2, day3 = (DATA_START.date() + timedelta(days=d) for d in range(3))

        def at(day, hh: int, mm: int, ss: int = 0) -> datetime:
            return datetime(day.year, day.month, day.day, hh, mm, ss, tzinfo=UTC)

        self.deploy(
            at(day1, 10, 12, 3), "catalog", "5.2.0", "catalog v5.2.0: search ranking tweaks"
        )
        self.deploy(
            INVENTORY_LEAK_DEPLOY,
            "inventory",
            "3.8.0",
            "inventory v3.8.0: cache product lookups to cut DB reads",
        )
        self.deploy(
            at(day1, 16, 5, 51),
            "payments",
            "2.13.2",
            "payments v2.13.2: retry provider timeouts once",
            "db pool initialised max_connections=20",
        )
        self.change(
            at(day1, 17, 40),
            "gateway",
            "config",
            "raise rate limit for /api/search to 600 rpm",
            actor="j.okafor",
        )
        self.deploy(
            at(day1, 20, 10, 9), "notifications", "1.9.3", "notifications v1.9.3: template fixes"
        )
        self.deploy(at(day2, 11, 2, 30), "auth", "4.1.0", "auth v4.1.0: rotate signing keys")
        self.deploy(
            INVENTORY_FIX_DEPLOY,
            "inventory",
            "3.8.1",
            "inventory v3.8.1: bound product cache size",
        )
        self.change(
            at(day2, 21, 15),
            "orders",
            "scale",
            "desired count 2 -> 2 (no-op autoscaling check)",
            actor="autoscaler",
        )
        self.change(
            at(day3, 5, 50, 12),
            "checkout",
            "config",
            "feature flag new-cart-ui rollout 0% -> 10%",
            actor="m.silva",
        )
        self.deploy(
            PAYMENTS_DEPLOY,
            "payments",
            "2.14.0",
            "payments v2.14.0: upgrade postgres driver, refactor config loading",
            "db pool initialised max_connections=5",
        )
        for instance, restarts in OOM_RESTARTS.items():
            for ts in restarts:
                self.log(
                    ts - timedelta(seconds=3),
                    instance,
                    "ERROR",
                    "FATAL ERROR: Reached heap limit Allocation failed - "
                    "JavaScript heap out of memory",
                )
                self.change(
                    ts + timedelta(seconds=20),
                    "inventory",
                    "restart",
                    f"task {instance} stopped (exit code 137, OutOfMemoryError: container "
                    "killed); replacement task started",
                    actor="ecs",
                )
                self.log(
                    ts + timedelta(seconds=55), instance, "INFO", "service starting version=3.8.0"
                )

    def background_minute(self, minute: datetime) -> None:
        load = traffic(minute)
        rng = self.rng
        for spec in SERVICES:
            for instance in spec.instance_names:
                for _ in range(self.count(spec.logs_per_min * load)):
                    ts = self.jitter(minute)
                    self.background_line(ts, spec, instance)
        # Low-rate noise that is *not* an incident.
        if rng.random() < 0.004:
            self.log(
                self.jitter(minute),
                "notifications-1",
                "WARN",
                "smtp send deferred: 421 try again later; will retry",
                attempt=rng.randint(1, 3),
            )
        if rng.random() < 0.01 * load:
            self.log(
                self.jitter(minute),
                rng.choice(SERVICE_BY_NAME["catalog"].instance_names),
                "WARN",
                f"cache miss ratio {rng.randint(31, 44)}% above threshold 30%",
            )
        if rng.random() < 0.006 * load:
            self.log(
                self.jitter(minute),
                rng.choice(SERVICE_BY_NAME["gateway"].instance_names),
                "ERROR",
                f"upstream catalog reset connection on GET /api/products/sku-"
                f"{rng.randint(1000, 9999)}; retried ok",
            )

    def background_line(self, ts: datetime, spec: ServiceSpec, instance: str) -> None:
        rng = self.rng
        req = self._id("req")
        match spec.name:
            case "gateway":
                path = rng.choice(["products", "search", "cart", "checkout", "orders", "login"])
                status = 404 if rng.random() < 0.02 else 200
                self.log(ts, instance, "INFO", f"GET /api/{path} {status} {self._ms(90)}ms", req)
            case "auth":
                if rng.random() < 0.05:
                    self.log(
                        ts, instance, "WARN", f"invalid credentials for {self._id('usr')}", req
                    )
                else:
                    self.log(ts, instance, "INFO", f"token issued for {self._id('usr')}", req)
            case "catalog":
                self.log(
                    ts,
                    instance,
                    "INFO",
                    f"product page served sku-{rng.randint(1000, 9999)} in {self._ms(110)}ms",
                    req,
                )
            case "checkout":
                self.log(
                    ts,
                    instance,
                    "INFO",
                    f"checkout completed {self._id('ord')} in {self._ms(260)}ms",
                    req,
                )
            case "payments":
                self.log(
                    ts,
                    instance,
                    "INFO",
                    f"payment authorized {self._id('pay')} in {self._ms(120)}ms",
                    req,
                    provider="cardstream",
                )
            case "orders":
                self.log(ts, instance, "INFO", f"order {self._id('ord')} created", req)
            case "inventory":
                self.log(
                    ts,
                    instance,
                    "INFO",
                    f"stock reserved sku-{rng.randint(1000, 9999)} qty={rng.randint(1, 4)}",
                    req,
                )
            case "notifications":
                self.log(ts, instance, "INFO", f"email queued for {self._id('usr')}", req)

    def payments_incident_minute(self, minute: datetime) -> None:
        s = payments_saturation(minute)
        if s <= 1:
            return
        rng = self.rng
        payments = SERVICE_BY_NAME["payments"].instance_names
        for _ in range(self.count(min(30.0, (s - 1) * 60))):
            instance = rng.choice(payments)
            wait = min(5000, self._ms(400 + (s - 1) * 9000, 0.3))
            self.log(
                self.jitter(minute),
                instance,
                "WARN",
                f"db pool wait {wait}ms (5/5 connections in use, {rng.randint(3, 40)} waiting)",
                self._id("req"),
            )
        for _ in range(self.count(min(25.0, max(0.0, s - 1.08) * 45))):
            ts = self.jitter(minute)
            req = self._id("req")
            order = self._id("ord")
            self.log(
                ts,
                rng.choice(payments),
                "ERROR",
                "TimeoutError: acquiring db connection timed out after 5000ms",
                req,
                provider="cardstream",
            )
            self.log(
                ts + timedelta(milliseconds=rng.randint(5, 40)),
                rng.choice(SERVICE_BY_NAME["checkout"].instance_names),
                "ERROR",
                f"payment authorization failed for {order}: upstream payments timed out "
                "after 5000ms",
                req,
            )
            if rng.random() < 0.6:
                self.log(
                    ts + timedelta(milliseconds=rng.randint(50, 120)),
                    rng.choice(SERVICE_BY_NAME["gateway"].instance_names),
                    "ERROR",
                    f"POST /api/checkout 502 {self._ms(5300, 0.05)}ms",
                    req,
                )
        for _ in range(self.count(min(20.0, (s - 1) * 40))):
            self.log(
                self.jitter(minute),
                rng.choice(SERVICE_BY_NAME["checkout"].instance_names),
                "WARN",
                f"slow upstream payments: responded in {self._ms(900 + (s - 1) * 8000)}ms",
                self._id("req"),
            )

    def inventory_incident_minute(self, minute: datetime) -> None:
        rng = self.rng
        for instance in SERVICE_BY_NAME["inventory"].instance_names:
            used = inventory_memory(minute, instance)
            if used > 0.8 * HEAP_LIMIT_MB and minute.minute % 10 == 0:
                pct = min(99, round(100 * used / HEAP_LIMIT_MB))
                self.log(self.jitter(minute), instance, "WARN", f"heap usage {pct}% of limit")
            for restart in OOM_RESTARTS[instance]:
                if restart <= minute < restart + timedelta(minutes=2):
                    for _ in range(self.count(4 * traffic(minute))):
                        self.log(
                            self.jitter(minute),
                            rng.choice(SERVICE_BY_NAME["checkout"].instance_names),
                            "WARN",
                            "inventory reservation failed: upstream unavailable (503); retrying",
                            self._id("req"),
                        )

    def metrics(self) -> None:
        rng = self.rng
        ts = DATA_START
        while ts < DATA_END:
            load = traffic(ts)
            s = payments_saturation(ts)
            pool_delay = max(0.0, s - 1) * 9000 if s > 1 else 0.0
            timeout_pct = min(35.0, max(0.0, s - 1.08) * 60) if s > 1 else 0.0
            for spec in SERVICES:
                for instance in spec.instance_names:
                    latency = spec.latency_ms
                    errors = 0.1
                    if spec.name == "payments":
                        latency = min(5000.0, latency + pool_delay)
                        errors += timeout_pct
                    elif spec.name == "checkout":
                        latency = min(5400.0, latency + pool_delay * 1.05)
                        errors += timeout_pct * 0.9
                    elif spec.name == "gateway":
                        latency += pool_delay * 0.25
                        errors += timeout_pct * 0.3
                    rate = spec.logs_per_min * 40 * load
                    cpu = 8 + 45 * load
                    memory = (
                        inventory_memory(ts, instance)
                        if spec.name == "inventory"
                        else spec.memory_mb
                    )
                    values = {
                        "latency_p95_ms": latency * rng.uniform(0.85, 1.15),
                        "error_rate_pct": errors * rng.uniform(0.7, 1.3),
                        "request_rate": rate * rng.uniform(0.9, 1.1),
                        "cpu_pct": cpu * rng.uniform(0.9, 1.1),
                        "memory_mb": memory * rng.uniform(0.98, 1.02),
                    }
                    for metric, value in values.items():
                        self.data.metrics.append(
                            (_epoch_ms(ts), spec.name, instance, metric, round(value, 2))
                        )
            ts += METRIC_STEP


SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE services (
    name TEXT PRIMARY KEY, description TEXT, instances TEXT, depends_on TEXT
);
CREATE TABLE logs (
    ts INTEGER NOT NULL, service TEXT NOT NULL, instance TEXT NOT NULL, level TEXT NOT NULL,
    message TEXT NOT NULL, template TEXT NOT NULL, attributes TEXT NOT NULL,
    request_id TEXT, trace_id TEXT
);
CREATE INDEX logs_ts ON logs (ts);
CREATE INDEX logs_service_ts ON logs (service, ts);
CREATE TABLE metric_defs (name TEXT PRIMARY KEY, unit TEXT, description TEXT);
CREATE TABLE metrics (
    ts INTEGER NOT NULL, service TEXT NOT NULL, instance TEXT NOT NULL,
    metric TEXT NOT NULL, value REAL NOT NULL
);
CREATE INDEX metrics_lookup ON metrics (metric, service, ts);
CREATE TABLE changes (
    ts INTEGER NOT NULL, service TEXT NOT NULL, kind TEXT NOT NULL, description TEXT NOT NULL,
    version TEXT, actor TEXT
);
CREATE TABLE known_issues (
    id TEXT PRIMARY KEY, title TEXT, signature TEXT, services TEXT, guidance TEXT
);
-- Answers for evaluation. Never read by the telemetry backend.
CREATE TABLE ground_truth (id TEXT PRIMARY KEY, body TEXT NOT NULL);
"""


def _epoch_ms(ts: datetime) -> int:
    return round(ts.timestamp() * 1000)


def generate(path: Path, seed: int = DEFAULT_SEED) -> Path:
    """Write the demo database to `path`, replacing any existing file."""
    data = Generator(seed).run()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    try:
        con.executescript(SCHEMA)
        con.executemany(
            "INSERT INTO meta VALUES (?, ?)",
            [
                ("name", "Acme Commerce (synthetic)"),
                ("data_start", DATA_START.isoformat()),
                ("data_end", DATA_END.isoformat()),
                ("seed", str(seed)),
                ("generator_version", GENERATOR_VERSION),
            ],
        )
        con.executemany(
            "INSERT INTO services VALUES (?, ?, ?, ?)",
            [
                (s.name, s.description, json.dumps(s.instance_names), json.dumps(s.depends_on))
                for s in SERVICES
            ],
        )
        con.executemany("INSERT INTO logs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", data.logs)
        con.executemany("INSERT INTO metric_defs VALUES (?, ?, ?)", METRIC_DEFS)
        con.executemany("INSERT INTO metrics VALUES (?, ?, ?, ?, ?)", data.metrics)
        con.executemany("INSERT INTO changes VALUES (?, ?, ?, ?, ?, ?)", data.changes)
        con.executemany(
            "INSERT INTO known_issues VALUES (?, ?, ?, ?, ?)",
            [
                (k["id"], k["title"], k["signature"], json.dumps(k["services"]), k["guidance"])
                for k in KNOWN_ISSUES
            ],
        )
        con.executemany(
            "INSERT INTO ground_truth VALUES (?, ?)",
            [(g["id"], json.dumps(g, sort_keys=True)) for g in GROUND_TRUTH],
        )
        con.commit()
        con.execute("VACUUM")
    finally:
        con.close()
    tmp.replace(path)
    return path
