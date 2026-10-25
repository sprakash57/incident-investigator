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
uv run iinv config
```

## Development

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

## License

MIT
