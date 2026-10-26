"""Live record of an investigation: the plan and every tool call, as it happens."""

import itertools
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class ToolCallRecord(BaseModel):
    id: str = Field(description="Short id the report cites as evidence, e.g. 't3'.")
    tool: str
    args: dict[str, Any]
    started_at: datetime
    latency_ms: float | None = None
    result: Any = None
    error: str | None = None


class PlanRecord(BaseModel):
    steps: list[str]
    recorded_at: datetime


TraceListener = Callable[[ToolCallRecord | PlanRecord], None]


class Trace:
    """Collects tool calls (thread-safe: sync tools may run concurrently) and notifies listeners."""

    def __init__(self, listener: TraceListener | None = None) -> None:
        self.calls: list[ToolCallRecord] = []
        self.plans: list[PlanRecord] = []
        self._listener = listener
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def start(self, tool: str, args: dict[str, Any]) -> ToolCallRecord:
        with self._lock:
            record = ToolCallRecord(
                id=f"t{next(self._ids)}", tool=tool, args=args, started_at=datetime.now(UTC)
            )
            self.calls.append(record)
        return record

    def finish(self, record: ToolCallRecord) -> None:
        record.latency_ms = round((datetime.now(UTC) - record.started_at).total_seconds() * 1000, 1)
        self._notify(record)

    def add_plan(self, steps: list[str]) -> PlanRecord:
        plan = PlanRecord(steps=steps, recorded_at=datetime.now(UTC))
        with self._lock:
            self.plans.append(plan)
        self._notify(plan)
        return plan

    def call_ids(self) -> set[str]:
        return {call.id for call in self.calls}

    def _notify(self, item: ToolCallRecord | PlanRecord) -> None:
        if self._listener:
            self._listener(item)
