"""Human-readable output: one-line tool call summaries and the Markdown incident report."""

from typing import Any

from incident_investigator.agent.report import IncidentReport
from incident_investigator.agent.trace import ToolCallRecord
from incident_investigator.runs import RunFile


def _short(value: Any) -> str:
    if isinstance(value, list):
        return ",".join(_short(v) for v in value)
    if hasattr(value, "isoformat"):
        return value.strftime("%m-%d %H:%M")
    return str(value)


def describe_call(call: ToolCallRecord) -> str:
    """`search_logs(services=payments levels=ERROR) → 30 lines (more available)`."""
    args = " ".join(f"{k}={_short(v)}" for k, v in call.args.items())
    return f"{call.tool}({args}) → {summarize_result(call)}"


def summarize_result(call: ToolCallRecord) -> str:
    if call.error:
        return f"error: {call.error}"
    result = call.result or {}
    if "unavailable" in result:
        return "not available in this backend"
    more = " (more available)" if result.get("truncated") else ""
    match call.tool:
        case "search_logs":
            return f"{len(result['lines'])} lines{more}"
        case "aggregate_logs":
            return f"{len(result['rows'])} rows{more}"
        case "get_metrics":
            points = sum(len(s["points"]) for s in result["series"])
            return f"{len(result['series'])} series, {points} points"
        case "get_changes":
            return f"{len(result['changes'])} changes"
        case "get_topology":
            return f"{len(result['services'])} services"
        case "known_issues":
            return f"{len(result['known_issues'])} known issues"
        case "describe_environment":
            return f"{len(result['services'])} services, now={result['now']}"
        case _:
            return "ok"


def _cite(ids: list[str]) -> str:
    return f" `[{', '.join(ids)}]`" if ids else ""


def report_markdown(report: IncidentReport, run: RunFile | None = None) -> str:
    lines = [
        "# Incident report",
        "",
        report.summary,
        "",
        "## Root cause hypothesis",
        "",
        report.root_cause_hypothesis,
        "",
        f"**Confidence:** {report.confidence.level.value} — {report.confidence.rationale}",
        "",
        "## Evidence",
        "",
    ]
    for item in report.evidence:
        lines.append(f"- {item.claim}{_cite(item.source_call_ids)}")
        if item.excerpt:
            lines.append(f"  > {item.excerpt}")
    lines += ["", "## Timeline (UTC)", ""]
    for entry in sorted(report.timeline, key=lambda e: e.time):
        lines.append(f"- **{entry.time:%m-%d %H:%M}** {entry.event}{_cite(entry.source_call_ids)}")
    lines += [
        "",
        "## Blast radius",
        "",
        f"Affected services: {', '.join(report.affected_services)}",
        "",
        report.blast_radius,
    ]
    if report.ruled_out:
        lines += ["", "## Ruled out", ""]
        for item in report.ruled_out:
            lines.append(f"- **{item.hypothesis}** — {item.reason}{_cite(item.source_call_ids)}")
    lines += ["", "## Recommended actions", ""]
    lines += [f"{i}. {action}" for i, action in enumerate(report.recommended_actions, 1)]
    if report.open_questions:
        lines += ["", "## Open questions", ""]
        lines += [f"- {q}" for q in report.open_questions]
    if run:
        lines += ["", "## Tool calls", ""]
        lines += [f"- `{call.id}` {describe_call(call)}" for call in run.tool_calls]
        lines += ["", f"_Run {run.id} · model {run.model} · backend {run.backend}_"]
    return "\n".join(lines) + "\n"
