"""Command-line entry point (`iinv`)."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, NoReturn

import pydantic_ai
import typer
from dotenv import load_dotenv
from pydantic_ai.exceptions import UserError
from pydantic_ai.models import Model
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

from incident_investigator import __version__
from incident_investigator.agent.deps import InvestigationDeps
from incident_investigator.agent.trace import PlanRecord, ToolCallRecord, Trace
from incident_investigator.config import get_settings
from incident_investigator.demo.generate import DEFAULT_SEED, generate
from incident_investigator.llm import resolve_model
from incident_investigator.render import describe_call, report_markdown
from incident_investigator.runs import run_and_record
from incident_investigator.telemetry.registry import UnknownBackendError, get_backend

app = typer.Typer(no_args_is_help=True, help="Investigate production incidents with an AI agent.")
console = Console()


def _print_version(value: bool) -> None:
    if value:
        console.print(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_print_version,
        is_eager=True,
        help="Show the version and exit.",
    ),
) -> None:
    # Provider SDKs read their API keys (OPENAI_API_KEY, ...) from the environment.
    load_dotenv(Path(".env"))
    pydantic_ai.BANNER_ENABLED = False  # the CLI owns its output


@app.command()
def config() -> None:
    """Show the effective settings."""
    settings = get_settings()
    table = Table(show_header=False, box=None)
    for name, value in settings.model_dump().items():
        table.add_row(f"[bold]{name}[/]", str(value) if value is not None else "[dim]unset[/]")
    console.print(table)


@app.command("generate-data")
def generate_data(
    out: Annotated[
        Path | None, typer.Option(help="Output path. Defaults to the configured sqlite_path.")
    ] = None,
    seed: Annotated[
        int, typer.Option(help="Random seed; same seed gives the same data.")
    ] = DEFAULT_SEED,
) -> None:
    """Generate the synthetic "Acme Commerce" demo dataset."""
    path = generate(out or get_settings().sqlite_path, seed)
    console.print(f"Wrote demo dataset to [bold]{path}[/] ({path.stat().st_size / 1e6:.1f} MB)")


def _fail(message: str) -> NoReturn:
    console.print(f"[bold red]Error:[/] {message}")
    raise typer.Exit(1)


def _model_for(name: str) -> Model | str:
    """Resolve the configured model. A seam so tests can substitute a scripted model."""
    return resolve_model(name)


def _print_trace_item(item: ToolCallRecord | PlanRecord) -> None:
    if isinstance(item, PlanRecord):
        console.print("[bold]Plan[/]")
        for i, step in enumerate(item.steps, 1):
            console.print(f"  {i}. {step}")
        return
    style = "red" if item.error else "cyan"
    console.print(
        f"[{style}]{item.id:>4}[/] {describe_call(item)} [dim]{item.latency_ms:.0f}ms[/]",
        highlight=False,
    )


@app.command()
def investigate(
    question: Annotated[str, typer.Argument(help="What is going wrong, in plain language.")],
    model: Annotated[
        str | None, typer.Option(help="PydanticAI model string; overrides INVESTIGATOR_MODEL.")
    ] = None,
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Also save the report as Markdown.")
    ] = None,
) -> None:
    """Investigate an incident and print a structured report."""
    settings = get_settings()
    model_name = model or settings.model
    if not model_name:
        _fail("no model configured: set INVESTIGATOR_MODEL in .env or pass --model")
    try:
        backend = get_backend(settings)
        info = backend.describe()
    except (FileNotFoundError, UnknownBackendError) as exc:
        _fail(str(exc))
    now = settings.now or min(datetime.now(UTC), info.data_end)

    console.print(f"[bold]Investigating:[/] {question}")
    console.print(
        f"[dim]model {model_name} · backend {info.name} · now {now:%Y-%m-%d %H:%M} UTC[/]\n"
    )
    deps = InvestigationDeps(backend=backend, now=now, trace=Trace(_print_trace_item))
    with console.status("Investigating…"):
        try:
            run = run_and_record(
                question,
                deps,
                model=_model_for(model_name),
                max_tool_calls=settings.max_tool_calls,
                max_output_tokens=settings.max_output_tokens,
            )
        except UserError as exc:
            _fail(f"model setup failed: {exc}")
    path = run.save(settings.runs_dir)

    if run.report:
        console.print()
        console.print(Markdown(report_markdown(run.report)))
        if output:
            output.write_text(report_markdown(run.report, run))
    usage = run.usage
    cost = f"${usage.cost_usd:.4f}" if usage and usage.cost_usd is not None else "unknown"
    tokens = f"{usage.input_tokens:,} in / {usage.output_tokens:,} out" if usage else "n/a"
    console.print(
        f"\n[dim]{len(run.tool_calls)} tool calls · {run.duration_s:.1f}s · tokens {tokens} · "
        f"cost {cost}\nRun saved to {path}[/]"
    )
    if output and run.report:
        console.print(f"[dim]Report saved to {output}[/]")
    if run.error:
        _fail(f"investigation did not complete: {run.error}")
