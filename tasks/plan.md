# Implementation Plan: Slice 1 — thin end-to-end agent path

Source: `docs/design.md` §9 step 1.

## Overview

Get one real investigation working end to end: a seeded synthetic dataset with two planted
incidents in SQLite, behind the backend-neutral `TelemetryBackend` interface; thin tools over
that interface; a PydanticAI agent that returns an `IncidentReport`; and
`iinv investigate "<description>"` with a live trace that saves a run file.

**Done when** `iinv investigate "checkout is slow since 06:30"` on `openai:gpt-4o-mini`
returns a grounded report naming the planted root cause, and `pytest` + `ruff` pass with no
API key.

## Architecture decisions

- **Backend-neutral models in `telemetry/models.py`**, the `TelemetryBackend` Protocol in
  `telemetry/base.py`, adapters in `telemetry/<name>.py`, chosen through a small registry.
  Nothing outside `telemetry/sqlite.py` imports `sqlite3`.
- **Time ranges are mandatory and capped** (default 24 h per query); results are capped and
  carry a `truncated` flag. Enforced in the shared request models, so every adapter inherits it.
- **Capabilities in `describe()`**: tools answer "not available in this backend" rather than
  failing when an adapter lacks metrics/changes.
- **Dataset**: fictional "Acme Commerce", ~7 services, 2 days of background noise, two planted
  incidents for this slice:
  1. *Connection-pool exhaustion after a deploy* — `payments` v2.14.0 deployed 06:22 lowers
     the DB pool size → pool timeouts in `payments` → `checkout` p95 latency climbs from 06:30.
     (The prompt's target incident.)
  2. *Memory leak → OOM restarts* on one `inventory` instance the previous afternoon.
  Seeded RNG, so the DB is reproducible; generated into `data/demo.db` and committed (small).
- **Investigation clock**: the reference "now" defaults to the end of the dataset span
  (from `describe()`), overridable by config.
- **Agent**: single PydanticAI agent, `output_type=IncidentReport`, backend passed as deps.
  Plan-then-act lives in the instructions (agent writes a short plan first; captured in the
  trace) — no separate planning call in this slice. `UsageLimits(tool_calls_limit=max_tool_calls)`;
  tool errors become `ModelRetry`/messages, not crashes.
- **Evidence grounding**: each tool call gets a short id (`t1`, `t2`, …) returned with its
  result; `Evidence.source` must cite one; the recorder flags evidence citing unknown ids.
- **Run files**: JSON in `runs/` (git-ignored) — input, model, timestamps, every tool call
  (id, name, args, result, latency), report, usage, cost estimate (via `genai-prices`, which
  ships with pydantic-ai; "unknown" when no price).
- **Tests**: PydanticAI `TestModel`/`FunctionModel` only; `ALLOW_MODEL_REQUESTS = False` in
  `conftest.py` so a real call fails the suite.

## Task list

### Phase 1: Data layer
- [ ] T1: Telemetry models + `TelemetryBackend` Protocol + registry
- [ ] T2: Seeded synthetic data generator + `iinv generate-data`
- [ ] T3: `SQLiteBackend` + adapter contract test suite

### Checkpoint A
- [ ] `pytest`, `ruff check`, `ruff format --check` pass
- [ ] `iinv generate-data` twice → byte-identical DB; the planted incidents are visible
      via the backend (spot-check queries)

### Phase 2: Agent path
- [ ] T4: Agent tools over the backend (compact JSON, call ids, caps)
- [ ] T5: `IncidentReport` + agent (instructions, limits, error handling)
- [ ] T6: Run recorder + run file
- [ ] T7: `iinv investigate` with live trace and rendered report

### Checkpoint B (review with owner)
- [ ] All tests + lint pass with no API key
- [ ] Real run on `gpt-4o-mini` names the payments deploy / pool exhaustion as root cause
      and cites real tool-call ids
- [ ] Run file written to `runs/`, nothing new outside it untracked

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| `gpt-4o-mini` too weak to reason across logs+metrics+changes | High | Tools return pre-aggregated, compact data; clear instructions; if it still fails, report honestly and test a stronger model rather than tuning the data to the model |
| Dataset too easy (root cause readable from one log line) | Med | Cause must be inferred by joining a change event, pool-timeout logs in another service and a latency metric |
| PydanticAI 2.x API differs from what I expect | Med | Read the installed package's API/AGENTS.md before T5; verify with `TestModel` |
| Real API cost | Low | Usage limits; one manual run per checkpoint |

## Open questions

None blocking. `tasks/` is a working file for this build and is not committed unless you
want it in the repo.
