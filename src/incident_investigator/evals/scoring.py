"""Scores one run against its scenario."""

from pydantic import BaseModel

from incident_investigator.evals.scenarios import Scenario
from incident_investigator.render import report_markdown
from incident_investigator.runs import RunFile

# Process checks: tools a sound investigation uses, whatever the incident.
EXPECTED_TOOLS = ("search_logs", "known_issues")


class RunScore(BaseModel):
    scenario_id: str
    run_id: str
    completed: bool
    error: str | None
    checks: dict[str, bool]
    tool_calls: int
    repeats: int
    duration_s: float
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None

    @property
    def passed(self) -> bool:
        return self.completed and all(self.checks.values())


def score_run(scenario: Scenario, run: RunFile) -> RunScore:
    checks: dict[str, bool] = {}
    report = run.report
    if report is not None:
        focus = f"{report.summary}\n{report.root_cause_hypothesis}".lower()
        full = report_markdown(report).lower()
        for check in scenario.checks:
            text = full if check.in_full_report else focus
            found = any(term.lower() in text for term in check.any_of)
            checks[check.name] = found if check.expect else not found
    else:
        checks = {check.name: False for check in scenario.checks}
    used = {call.tool for call in run.tool_calls}
    for tool in EXPECTED_TOOLS:
        checks[f"used {tool}"] = tool in used
    checks["citations all real"] = report is not None and not run.unknown_citations

    usage = run.usage
    return RunScore(
        scenario_id=scenario.id,
        run_id=run.id,
        completed=report is not None,
        error=run.error,
        checks=checks,
        tool_calls=len(run.tool_calls),
        repeats=sum(1 for c in run.tool_calls if c.result and "duplicate_of" in c.result),
        duration_s=round(run.duration_s, 1),
        input_tokens=usage.input_tokens if usage else None,
        output_tokens=usage.output_tokens if usage else None,
        cost_usd=usage.cost_usd if usage else None,
    )
