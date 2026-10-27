"""Runs scenarios repeatedly against one model and summarises the scores."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean

from pydantic import BaseModel
from pydantic_ai.models import Model

from incident_investigator.agent.deps import InvestigationDeps
from incident_investigator.agent.trace import Trace
from incident_investigator.evals.scenarios import Scenario
from incident_investigator.evals.scoring import RunScore, score_run
from incident_investigator.runs import run_and_record
from incident_investigator.telemetry.base import TelemetryBackend


class EvalResult(BaseModel):
    id: str
    model: str
    started_at: datetime
    repeats: int
    scores: list[RunScore]

    def save(self, out_dir: Path) -> tuple[Path, Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        json_path = out_dir / "results.json"
        md_path = out_dir / "results.md"
        json_path.write_text(self.model_dump_json(indent=2))
        md_path.write_text(summary_markdown(self))
        return json_path, md_path


def run_eval(
    scenarios: list[Scenario],
    *,
    model_name: str,
    model_for: Callable[[], Model | str],
    backend: TelemetryBackend,
    now: datetime,
    repeats: int,
    max_tool_calls: int,
    max_output_tokens: int | None,
    runs_dir: Path,
    on_score: Callable[[RunScore], None] | None = None,
) -> EvalResult:
    started = datetime.now(UTC)
    result = EvalResult(
        id=f"{started:%Y%m%dT%H%M%SZ}-{model_name.rsplit(':', 1)[-1].replace('/', '_')}",
        model=model_name,
        started_at=started,
        repeats=repeats,
        scores=[],
    )
    for scenario in scenarios:
        for _ in range(repeats):
            deps = InvestigationDeps(backend=backend, now=now, trace=Trace())
            run = run_and_record(
                scenario.question,
                deps,
                model=model_for(),
                max_tool_calls=max_tool_calls,
                max_output_tokens=max_output_tokens,
            )
            run.save(runs_dir / result.id)
            score = score_run(scenario, run)
            result.scores.append(score)
            if on_score:
                on_score(score)
    return result


def _rate(values: list[bool]) -> str:
    return f"{sum(values)}/{len(values)}"


def _avg(values: list[float | int | None], fmt: str) -> str:
    present = [v for v in values if v is not None]
    return format(mean(present), fmt) if present else "n/a"


def summary_markdown(result: EvalResult) -> str:
    lines = [
        f"# Eval {result.id}",
        "",
        f"Model `{result.model}` · {result.repeats} run(s) per scenario · "
        f"{result.started_at:%Y-%m-%d %H:%M} UTC · synthetic Acme Commerce dataset",
        "",
        "| Scenario | Passed | Avg tool calls | Avg repeats | Avg duration | Avg cost |",
        "|---|---|---|---|---|---|",
    ]
    by_scenario: dict[str, list[RunScore]] = {}
    for score in result.scores:
        by_scenario.setdefault(score.scenario_id, []).append(score)
    for scenario_id, scores in by_scenario.items():
        costs = [s.cost_usd for s in scores]
        lines.append(
            f"| {scenario_id} | {_rate([s.passed for s in scores])} "
            f"| {_avg([s.tool_calls for s in scores], '.1f')} "
            f"| {_avg([s.repeats for s in scores], '.1f')} "
            f"| {_avg([s.duration_s for s in scores], '.0f')}s "
            f"| {'$' + _avg(costs, '.4f') if any(c is not None for c in costs) else 'unknown'} |"
        )
    for scenario_id, scores in by_scenario.items():
        lines += ["", f"## {scenario_id}", "", "| Check | Passed |", "|---|---|"]
        lines.append(f"| completed with a report | {_rate([s.completed for s in scores])} |")
        for name in scores[0].checks:
            lines.append(f"| {name} | {_rate([s.checks[name] for s in scores])} |")
        errors = sorted({s.error.split(":", 1)[0] for s in scores if s.error})
        if errors:
            lines += ["", f"Errors: {', '.join(errors)}"]
    return "\n".join(lines) + "\n"
