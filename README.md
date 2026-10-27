# Incident Investigator

An AI assistant for on-call engineers. Describe an incident in plain language
("checkout has been slow since 06:30") and it plans an investigation, pulls evidence from
logs, metrics, service topology and recent changes, runs ad-hoc analysis, and returns a
structured report: root-cause hypothesis, confidence, evidence chain, blast radius and
recommended next actions. A human stays in charge of what happens next.

> **Status:** early development. See [docs/design.md](docs/design.md) for the design.

## Design highlights

- **Pluggable telemetry backends.** The agent only talks to a backend interface. Ships with a
  synthetic demo backend; CloudWatch, Grafana Loki, OpenSearch and others plug in as adapters.
- **Any LLM provider.** Built on [PydanticAI](https://ai.pydantic.dev); pick the model with one
  setting.
- **Evidence over vibes.** Every claim in a report links back to the tool call that produced it.

## Quickstart

```bash
uv sync
cp .env.example .env   # set INVESTIGATOR_MODEL and your provider key
uv run iinv generate-data   # synthetic "Acme Commerce" dataset -> data/demo.db
uv run iinv config
uv run iinv investigate "checkout is slow since 06:30"
```

Each investigation prints a live trace of the agent's plan and tool calls, then the report,
and saves a JSON run file to `runs/` (git-ignored: run files contain raw log lines). Add
`-o report.md` to also save the report as Markdown.

## Evaluation

```bash
uv run iinv eval --model <model> --repeats 3
```

Runs each scenario (incidents planted in the synthetic dataset, with known root causes)
several times and scores every report: right culprit, right mechanism, timing explained,
red herring not blamed, logs actually read, known issues consulted, no made-up citations.
Prints a summary table and saves it, with every run file, under `runs/evals/`.

The checks are deterministic text checks, a floor rather than a judge: a report can pass
them and still be weak, so read the run files too. No results are published here yet.

## Development

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

## License

MIT
