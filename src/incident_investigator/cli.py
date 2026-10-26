"""Command-line entry point (`iinv`)."""

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from incident_investigator import __version__
from incident_investigator.config import get_settings
from incident_investigator.demo.generate import DEFAULT_SEED, generate

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
    pass


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
