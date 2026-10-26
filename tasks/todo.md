# Slice 1 — tasks

## T1: Telemetry models + backend Protocol + registry — S/M
Backend-neutral pydantic models (TimeRange with cap, LogQuery/LogRecord/LogPage,
LogAggregation/AggregateRow, MetricQuery/MetricSeries, ServiceGraph, ChangeEvent,
KnownIssue, BackendInfo with capabilities), `TelemetryBackend` Protocol, `get_backend(settings)`.
- [ ] Time range required; ranges > cap rejected with a clear error
- [ ] Registry returns the configured backend; unknown name → clear error listing options
- [ ] Unit tests for validation + registry
Files: `src/incident_investigator/telemetry/{__init__,models,base,registry}.py`, `tests/test_telemetry_models.py`
Deps: none

## T2: Seeded synthetic data generator — M
"Acme Commerce": services + dependencies, instances, 2 days of background logs/metrics,
deploy/config/restart events, known issues, and the 2 planted incidents (+ ground truth
table). `iinv generate-data [--out] [--seed]`.
- [ ] Same seed → identical DB (byte-for-byte)
- [ ] Planted incidents present with ground truth rows
- [ ] DB small enough to commit (< ~10 MB)
Files: `src/incident_investigator/demo/generate.py`, `cli.py`, `tests/test_generate.py`, `data/demo.db`
Deps: T1 (schema shape mirrors models)

## T3: SQLiteBackend + contract suite — M
Implements every Protocol method; contract tests parametrized over backends.
- [ ] Contract suite: time-range filtering, limits + truncated flag, level/service/text filters,
      aggregation grouping/bucketing, metrics series, topology, changes, describe()
- [ ] SQLite passes; spot checks find both planted incidents
Files: `telemetry/sqlite.py`, `tests/contract/test_backend_contract.py`, `tests/conftest.py`
Deps: T1, T2

## Checkpoint A — tests, lint, determinism, spot checks

## T4: Agent tools — M
Tools: describe_environment, search_logs, aggregate_logs, get_metrics, get_topology,
get_changes, known_issues. Compact JSON, call ids, "not available" for missing capabilities,
validation errors → ModelRetry.
- [ ] Each tool tested directly against the demo DB
- [ ] Tool module imports nothing SQLite-specific
Files: `agent/tools.py`, `agent/deps.py`, `tests/test_tools.py`
Deps: T3

## T5: IncidentReport + agent — M
- [ ] `IncidentReport` model per design §6.3
- [ ] Agent built from settings (model string), instructions with plan-then-act + evidence rules
- [ ] UsageLimits from `max_tool_calls`; TestModel run completes; FunctionModel test checks a
      tool error is returned to the model, not raised
Files: `agent/report.py`, `agent/agent.py`, `agent/prompts.py`, `tests/test_agent.py`, `tests/conftest.py`
Deps: T4

## T6: Run recorder — S/M
- [ ] Run file JSON: input, model, plan, tool calls (id/name/args/result/latency), report, usage, cost
- [ ] Evidence citing unknown tool-call ids is flagged
- [ ] Round-trip load test
Files: `runs.py` (or `agent/recording.py`), `tests/test_runs.py`
Deps: T5

## T7: `iinv investigate` — M
- [ ] Live rich trace (plan, each tool call + short result summary), final markdown report
- [ ] Saves run file; `--output report.md` optional
- [ ] Clear error when model/key unset; CLI test with TestModel override
Files: `cli.py`, `render.py`, `tests/test_cli.py`
Deps: T6

## Checkpoint B — real gpt-4o-mini run, review with owner
