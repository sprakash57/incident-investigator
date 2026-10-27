"""Evaluation scenarios over the synthetic dataset, with what a correct report must say.

Checks are deliberately simple text checks on the report's summary and root-cause hypothesis
(plus a few process checks in `scoring.py`). They are a floor, not a judge: a report can pass
them and still be weak, which is why run files are kept for reading.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Check:
    name: str
    any_of: tuple[str, ...]  # case-insensitive; passes if any appears
    expect: bool = True  # False: none of the terms may appear (e.g. a red herring blamed)
    in_full_report: bool = False  # search the whole report, not just summary + root cause


@dataclass(frozen=True)
class Scenario:
    id: str
    question: str
    ground_truth_id: str  # row in the demo database's ground_truth table
    checks: tuple[Check, ...]


SCENARIOS = (
    Scenario(
        id="checkout-slow",
        question="checkout is slow since 06:30",
        ground_truth_id="payments-pool-exhaustion",
        checks=(
            Check("names payments", ("payments",)),
            Check("names deploy v2.14.0", ("2.14.0",)),
            Check("mechanism: connection pool", ("pool", "max_connections")),
            Check("timing: traffic ramp", ("traffic", "load", "ramp", "demand", "request volume")),
            Check("red herring not blamed", ("feature flag", "new-cart-ui"), expect=False),
        ),
    ),
    Scenario(
        id="inventory-restarts",
        question="inventory kept restarting yesterday and checkout saw reservation failures. "
        "What happened, and is it still happening?",
        ground_truth_id="inventory-memory-leak",
        checks=(
            Check("names deploy v3.8.0", ("3.8.0",)),
            Check("mechanism: memory leak", ("leak", "unbounded", "grew", "growing", "growth")),
            Check("outcome: out of memory", ("out of memory", "oom", "heap")),
            Check("notes fix v3.8.1", ("3.8.1",), in_full_report=True),
            Check("red herring not blamed", ("4.1.0", "signing key"), expect=False),
        ),
    ),
)

SCENARIOS_BY_ID = {s.id: s for s in SCENARIOS}
