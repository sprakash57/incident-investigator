"""Builds the investigation agent and runs one investigation."""

from dataclasses import dataclass

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RunUsage, UsageLimits

from incident_investigator.agent.deps import InvestigationDeps
from incident_investigator.agent.prompts import INSTRUCTIONS
from incident_investigator.agent.report import IncidentReport
from incident_investigator.agent.tools import TOOLS

TOOL_TIMEOUT_SECONDS = 30


def build_agent(model: Model | str | None = None) -> Agent[InvestigationDeps, IncidentReport]:
    agent = Agent(
        model,
        output_type=IncidentReport,
        deps_type=InvestigationDeps,
        instructions=INSTRUCTIONS,
        tools=TOOLS,
        retries=3,
        tool_timeout=TOOL_TIMEOUT_SECONDS,
        defer_model_check=True,
    )

    @agent.output_validator
    def evidence_must_cite_real_calls(
        ctx: RunContext[InvestigationDeps], report: IncidentReport
    ) -> IncidentReport:
        unknown = report.cited_call_ids() - ctx.deps.trace.call_ids()
        if unknown:
            raise ModelRetry(
                f"The report cites tool call ids that do not exist: {sorted(unknown)}. "
                "Cite only call_id values returned by your tool calls."
            )
        return report

    return agent


@dataclass
class Investigation:
    report: IncidentReport
    usage: RunUsage


def investigate(
    question: str,
    deps: InvestigationDeps,
    *,
    model: Model | str,
    max_tool_calls: int,
    max_output_tokens: int | None = None,
) -> Investigation:
    """Run the agent to completion. Raises `UsageLimitExceeded` if it runs out of tool calls."""
    deps.tool_budget = max_tool_calls
    settings = ModelSettings(max_tokens=max_output_tokens) if max_output_tokens else None
    result = build_agent(model).run_sync(
        question,
        deps=deps,
        model_settings=settings,
        usage_limits=UsageLimits(tool_calls_limit=max_tool_calls),
    )
    return Investigation(report=result.output, usage=result.usage)
