"""The structured incident report the agent must return."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

CallIds = list[str]
_CITE = "Ids of the tool calls (e.g. 't3') whose results support this."


class ConfidenceLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Confidence(BaseModel):
    level: ConfidenceLevel
    rationale: str = Field(description="Why this level: what is proven and what is not.")


class Evidence(BaseModel):
    claim: str = Field(description="One factual statement established by the data.")
    source_call_ids: CallIds = Field(min_length=1, description=_CITE)
    excerpt: str = Field(description="The specific data that shows it: a log line, values, …")


class TimelineEntry(BaseModel):
    time: datetime
    event: str
    source_call_ids: CallIds = Field(default_factory=list, description=_CITE)


class RuledOut(BaseModel):
    hypothesis: str
    reason: str
    source_call_ids: CallIds = Field(default_factory=list, description=_CITE)


class IncidentReport(BaseModel):
    summary: str = Field(description="Two or three sentences an on-call engineer reads first.")
    root_cause_hypothesis: str = Field(
        description="The most likely root cause: what changed or broke, where, and the mechanism."
    )
    confidence: Confidence
    evidence: list[Evidence] = Field(min_length=1)
    timeline: list[TimelineEntry] = Field(description="Key events in time order.")
    affected_services: list[str]
    blast_radius: str = Field(description="Who and what is impacted, and how badly.")
    ruled_out: list[RuledOut] = Field(
        default_factory=list, description="Plausible explanations you checked and rejected."
    )
    recommended_actions: list[str] = Field(
        description="Concrete next steps for a human, most urgent first. Do not claim to have "
        "done anything."
    )
    open_questions: list[str] = Field(
        default_factory=list, description="What the data could not answer."
    )

    def cited_call_ids(self) -> set[str]:
        items = [*self.evidence, *self.timeline, *self.ruled_out]
        return {call_id for item in items for call_id in item.source_call_ids}
