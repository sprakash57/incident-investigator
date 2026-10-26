"""What every tool receives through PydanticAI's RunContext."""

from dataclasses import dataclass, field
from datetime import datetime

from incident_investigator.agent.trace import Trace
from incident_investigator.telemetry.base import TelemetryBackend


@dataclass
class InvestigationDeps:
    backend: TelemetryBackend
    now: datetime  # the moment the investigation treats as "now" (UTC)
    trace: Trace = field(default_factory=Trace)
    tool_budget: int | None = None  # tool calls allowed in this run; reported back to the model
