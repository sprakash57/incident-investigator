"""Run files: a complete JSON record of one investigation.

A run file holds the question, the plan, every tool call with its arguments, result and
latency, the final report (or the error that stopped the run), token usage and an estimated
cost. Run files power replay, history and evals.

Tool results are raw telemetry, so a run against a real backend contains real log lines.
They are written to the git-ignored runs directory; only runs against the synthetic demo
backend belong in `examples/runs/`.
"""

import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_ai.exceptions import AgentRunError
from pydantic_ai.models import Model

from incident_investigator.agent.deps import InvestigationDeps
from incident_investigator.agent.investigator import investigate
from incident_investigator.agent.report import IncidentReport
from incident_investigator.agent.trace import PlanRecord, ToolCallRecord

FORMAT_VERSION = 1


class UsageSummary(BaseModel):
    requests: int
    tool_calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float | None = Field(description="Estimate from bundled prices; None if unknown.")


class RunFile(BaseModel):
    format_version: int = FORMAT_VERSION
    id: str
    question: str
    model: str
    backend: str
    now: datetime
    started_at: datetime
    finished_at: datetime
    plan: list[PlanRecord]
    tool_calls: list[ToolCallRecord]
    report: IncidentReport | None = None
    error: str | None = None
    usage: UsageSummary | None = None
    unknown_citations: list[str] = Field(
        default_factory=list, description="Call ids cited by the report that were never made."
    )

    @property
    def duration_s(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()

    def save(self, runs_dir: Path) -> Path:
        runs_dir.mkdir(parents=True, exist_ok=True)
        path = runs_dir / f"{self.id}.json"
        path.write_text(self.model_dump_json(indent=2))
        return path

    @classmethod
    def load(cls, path: Path) -> "RunFile":
        return cls.model_validate_json(path.read_text())


def run_and_record(
    question: str,
    deps: InvestigationDeps,
    *,
    model: Model | str,
    max_tool_calls: int,
) -> RunFile:
    """Run an investigation and capture everything about it, including a failed run."""
    started = datetime.now(UTC)
    report = error = usage = None
    try:
        result = investigate(question, deps, model=model, max_tool_calls=max_tool_calls)
    except AgentRunError as exc:
        error = f"{type(exc).__name__}: {exc}"
    else:
        report = result.report
        usage = UsageSummary(
            requests=result.usage.requests,
            tool_calls=result.usage.tool_calls,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            cost_usd=float(result.usage.cost) if result.usage.cost is not None else None,
        )
    known_ids = deps.trace.call_ids()
    return RunFile(
        id=f"{started:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}",
        question=question,
        model=model if isinstance(model, str) else model.model_name,
        backend=deps.backend.describe().name,
        now=deps.now,
        started_at=started,
        finished_at=datetime.now(UTC),
        plan=deps.trace.plans,
        tool_calls=deps.trace.calls,
        report=report,
        error=error,
        usage=usage,
        unknown_citations=sorted(report.cited_call_ids() - known_ids) if report else [],
    )
