"""Command-line entry point (`iinv`)."""

import typer
from rich.console import Console
from rich.table import Table

from incident_investigator import __version__
from incident_investigator.config import get_settings

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
