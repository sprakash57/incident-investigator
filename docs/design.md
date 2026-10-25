# Incident Investigator — Design

Date: 2026-10-25
Status: draft for approval

## 1. Intent

An AI assistant for on-call engineers. Given a natural-language incident description
("checkout has been slow since 06:30"), it plans an investigation, gathers evidence from
logs/metrics/service topology, runs ad-hoc analysis, looks up unfamiliar errors on the web,
and returns a structured incident report with a root-cause hypothesis, confidence, an
evidence chain, blast radius and recommended next actions. A human decides what to do.

**Why this repo exists:** it is the owner's flagship portfolio piece for AI Engineer /
Tech Lead / Engineering Manager roles and for consulting clients. It must read as
production-minded engineering, not a course notebook. It must also be a credible base for
real use: the owner runs a NestJS app on AWS ECS with CloudWatch Logs and wants to point
this at it later (CloudWatch adapter, Teams notifications, small web app) — that work is
*not* in today's scope but the architecture must make it a plug-in, not a rewrite.

## 2. Goals (today)

1. A new standalone public GitHub repo, `incident-investigator`, working end-to-end on a
   **synthetic demo** dataset that anyone can generate and run locally.
2. **Pluggable telemetry backends.** The agent depends only on an abstract backend
   interface. Ships with one implementation (SQLite synthetic). Grafana Loki, CloudWatch,
   Elasticsearch/OpenSearch, Datadog etc. must be addable as a new adapter module with no
   change to agent, tools, report, UI or evals.
3. **Provider-agnostic LLM** via PydanticAI; model selected by config/env
   (e.g. `openai:gpt-…`, `anthropic:claude-sonnet-5-5`, `bedrock:…`, `ollama:…`).
4. Two ways to use it: a **CLI** with a live step-by-step trace, and a **Streamlit web app**.
   Both support a **replay mode** that renders recorded runs with no API key.
5. An **evaluation harness** with scenarios that have known ground truth, producing a
   results table (root-cause accuracy, tool-use behaviour, latency, tokens/cost) for the README.
6. Engineering hygiene a hiring manager expects: package layout, typed code, unit tests that
   need no API key, lint, CI, Dockerfile, README with architecture diagram, short ADRs.
7. **Incremental git history**: many small, meaningful commits made as the work progresses.

## 3. Non-goals (today)

- No real backend adapters (CloudWatch, Loki, …) — only the interface, a documented
  "how to write an adapter" guide, and an adapter contract test suite that a new adapter
  can run against itself.
- No alert ingestion (SNS/Alertmanager webhooks), no Teams/Slack delivery, no auth, no
  hosted deployment. Leave clear extension points; mention them in a README roadmap.
- No multi-agent system, no LangGraph, no autonomous remediation.
- No use of course material: no course notebooks, datasets, problem text or branding;
  no mention of the course.

## 4. Anti-goals (failure even if it "works")

- Looks like a notebook pasted into a repo, or one giant commit.
- Agent/tool code that imports SQLite-specific things (breaks pluggability).
- Executing LLM-written Python in-process, or claiming the sandbox is a security boundary
  when it is not.
- README claims numbers that weren't measured, or eval scores from tests the agent can
  trivially pass.
- Any secret or API key committed. The course proxy key in the old `config.json` is never used
  or copied.
- Tests that call a real LLM (CI must be free and deterministic).

## 5. Constraints

- Python 3.12+, managed with `uv`. Lint/format with `ruff`; tests with `pytest`.
- Agent framework: **PydanticAI** (owner's decision). No LangChain/LangGraph.
- Must run fully offline except LLM calls and optional web search.
- Owner will push to GitHub themselves (`gh` is not installed). Repo is created locally under
  `/Users/Sunny/spacem/Olympus/incident-investigator`, as its own git repo.
- Commits are real, incremental, with honest timestamps (no backdating).

## 6. Architecture (decided)

```
            ┌──────────── CLI (typer + rich) ─────────┐   ┌── Streamlit app ──┐
            │  investigate "…"  | replay <run.json>   │   │ ask / replay / history │
            └──────────────────────┬──────────────────┘   └─────────┬─────────┘
                                   ▼                                ▼
                         investigator.run(...)  → emits step events (for live trace)
                                   │
                    PydanticAI Agent (provider from config), output_type=IncidentReport
                                   │ tools (thin, backend-agnostic)
   ┌───────────────┬───────────────┼────────────────┬──────────────┬──────────────┐
search_logs   aggregate_logs   get_metrics   get_topology   get_changes   known_issues
                                   │                               web_search  run_python
                                   ▼                                  │            │
                     TelemetryBackend (Protocol)                 search provider  sandbox
                       └── SQLiteBackend (synthetic)             (DDG default)    (subprocess
                       └── [future] CloudWatch / Loki / OpenSearch …               / docker)
```

### 6.1 Telemetry backend interface (the pluggability seam)

A `TelemetryBackend` Protocol with backend-neutral, typed request/response models
(pydantic). Methods (names indicative, builder may refine):

- `search_logs(query: LogQuery) -> LogPage` — time range (required), service(s), level(s),
  text contains, request/trace id, limit. Returns normalized `LogRecord`s
  (timestamp, service, host/instance, level, message, attributes dict, request_id, trace_id).
- `aggregate_logs(query: LogAggregation) -> list[AggregateRow]` — counts grouped by
  service/level/host/message-template over a time range, optional time bucketing.
- `get_metrics(query: MetricQuery) -> list[MetricSeries]` — e.g. latency p95, error rate,
  cpu, memory per service over time.
- `get_topology() -> ServiceGraph` — services and dependencies.
- `get_changes(time_range) -> list[ChangeEvent]` — deploys/config changes/restarts
  ("what changed" is a first-class signal; maps to ECS deployments, k8s rollouts, CI deploys).
- `known_issues() -> list[KnownIssue]` — runbook/known-error signatures.
- `describe() -> BackendInfo` — name, available services, data time span, capabilities
  (an adapter that lacks metrics or changes says so; the tools tell the agent "not available
  in this backend" instead of failing).

Rules: time ranges are mandatory and capped (protects real backends from huge scans);
results are size-capped and say when truncated. Tools convert backend models to compact
JSON for the LLM. Backend selection via config (`backend: sqlite`, plus backend-specific
settings); adapters register through a small registry so a new adapter is one module +
one registry entry.

An **adapter contract test suite** (parametrized pytest) validates any backend against the
interface using the synthetic data loaded into it; SQLite passes it today.

### 6.2 Synthetic dataset

A seeded, deterministic generator script creates a fictional SaaS ("Acme Commerce" or
similar — builder may name it) with ~6–8 services (gateway, auth, payments, orders,
inventory, notifications, reporting…), several instances, a dependency graph, ~1 week of
background logs/metrics with realistic noise, deploy/change events, and **planted
incidents**, each with a ground-truth root cause, e.g.:

- bad deploy → error spike in a downstream service
- memory leak → OOM restarts on one instance
- database connection-pool exhaustion → cascading timeouts
- third-party/vendor error with an unfamiliar code (forces web search)
- certificate expiry / auth token failures
- noisy-but-harmless warning storm (red herring — correct answer is "not the cause")
- slow-burn latency regression across the week (needs quantitative analysis)

The generated SQLite DB is reproducible from the script; whether the DB file is also
committed is left to the builder (small enough → commit for zero-friction demo).

### 6.3 Agent

- One PydanticAI agent, structured output `IncidentReport`:
  summary, root_cause_hypothesis, confidence (low/medium/high + rationale),
  evidence[] (claim, source tool call id, supporting data excerpt), timeline[],
  affected_services / blast radius, ruled_out[] (hypotheses rejected and why),
  recommended_actions[], open_questions[].
- Plan-then-act: the agent first writes a short plan (surfaced in the trace), then adapts
  its tool sequence. Builder decides whether planning is a separate call or part of the
  system prompt/first step.
- Guardrails: max tool calls / usage limits per run, per-tool timeouts, tool errors returned
  to the model as messages (not crashes).
- Every run is recorded as a **run file** (JSON: input, model, plan, every tool call with
  args/result/latency, final report, token usage, cost estimate). Run files power replay,
  the web app history, and evals.

### 6.4 Python analysis sandbox

LLM-written code never runs in the main process. A `Sandbox` interface with:
- `subprocess` backend (default): separate Python process, CPU/wall time limit, memory limit
  (rlimit where supported), working dir in a temp folder, data passed in as a file, no
  access to API keys (env scrubbed).
- `docker` backend (optional): same contract in a container with `--network none`,
  read-only filesystem, resource limits.
README/ADR states plainly that the subprocess backend is a guard against accidents, not a
security boundary; use docker for untrusted settings.

### 6.5 Web search

`SearchProvider` interface; default DuckDuckGo (no key), optional Tavily (key). Disable-able
via config. The agent is instructed to check `known_issues` before searching.

### 6.6 Interfaces

- **CLI** (`incident-investigator` / `iinv`): `investigate "<description>"` with live rich
  trace and final rendered report (markdown, optionally saved); `replay <run.json>`;
  `generate-data`; `eval`.
- **Streamlit app**: input box + live step trace + rendered report; replay of bundled
  example runs (works with no key); history of local runs.

### 6.7 Evaluation

- Scenario file (YAML/JSON): prompt, expected root cause (keywords + reference text),
  expected key evidence, tools expected/forbidden, red-herring flags.
- Scoring per scenario: root-cause correctness (LLM-as-judge against the reference, with
  keyword check as a sanity floor), evidence grounding (claims cite real tool calls),
  tool behaviour (e.g. checked known issues before web search), latency, tokens, cost.
- `eval` command writes a markdown results table + JSON; README embeds the latest table
  with the model name and date. Supports running across multiple models to compare.
- Unit tests use PydanticAI's test/function models — no real LLM in CI.

### 6.8 Repo hygiene

`src/` layout, `pyproject.toml`, `uv.lock`, ruff config, pytest, GitHub Actions
(lint + tests), Dockerfile, `.env.example`, `LICENSE` (MIT), `README.md` (problem, demo GIF/
screenshot placeholder, architecture diagram, quickstart in ≤3 commands, eval table,
"plugging in your own logs", roadmap: CloudWatch/ECS adapter, Loki adapter, alert webhooks,
Teams/Slack delivery, auth for the web app), `docs/adr/` (PydanticAI over LangGraph;
backend abstraction; sandbox approach; provider-agnostic models), `docs/adding-a-backend.md`.

## 7. Left to the builder

Exact module names, CLI library details, prompt wording, fictional company/service names,
incident timings, number of scenarios (6–8), tokenizer/cost table approach, styling of the
web app, whether planning is a separate LLM call, whether the DB file is committed.

## 7a. Calls made by the designer (owner may override)

- **Vendor-error scenario & web search:** the fictional company uses a *real* third-party
  service with a real public error code (e.g. AWS S3 `SlowDown` / a real payment-provider
  decline code), so live search can genuinely help in demos. Scored evals use a stubbed
  `SearchProvider` backed by recorded fixtures for reproducibility; the README says so.
- **Cut order if time runs short** (core path — backend, tools, agent, CLI, run files, replay,
  unit tests, README — is never cut): 1) multi-model eval comparison, 2) Docker sandbox
  backend, 3) Streamlit history view, 4) Dockerfile for the app. Anything cut goes in the
  README roadmap, not silently dropped.
- **No-key fallback:** if no API key is available today, the repo ships code-complete with
  tests; replay examples and the eval table are added in a later commit once real runs exist.
  No invented numbers in the meantime.
- **Investigation clock:** "now" is a configurable reference time; for the synthetic
  backend it defaults to the end of the dataset's time span.

## 8. Open items for the owner

- Which LLM API key/provider will be used to run evals and record the replay runs
  (the course proxy key must not be used).
- Git author name/email for commits (no global git identity configured on this machine).
