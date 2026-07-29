"""Revanent command-line entry point."""

from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from revanent import __version__
from revanent.utilities.tool_detection import CheckStatus, detect_environment

app = typer.Typer(
    name="revanent",
    help="Local-first software-engineering orchestration.",
    no_args_is_help=True,
)
console = Console()


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"revanent {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show version."),
    ] = False,
) -> None:
    """Run Revanent commands."""


@app.command()
def doctor(
    strict: Annotated[
        bool,
        typer.Option(help="Fail when a required or provider tool is unavailable."),
    ] = False,
) -> None:
    """Report supported runtime and provider CLI availability without changing state."""
    checks = detect_environment()
    table = Table(title="Revanent environment")
    table.add_column("Capability")
    table.add_column("Status")
    table.add_column("Detail")
    colors = {
        CheckStatus.AVAILABLE: "green",
        CheckStatus.UNAVAILABLE: "yellow",
        CheckStatus.UNSUPPORTED: "red",
    }
    for check in checks:
        table.add_row(check.name, f"[{colors[check.status]}]{check.status.value}[/]", check.detail)
    console.print(table)

    failures = [
        check for check in checks if check.required and check.status is not CheckStatus.AVAILABLE
    ]
    provider_gaps = [
        check
        for check in checks
        if check.category == "provider" and check.status is not CheckStatus.AVAILABLE
    ]
    if failures or (strict and provider_gaps):
        raise typer.Exit(code=1)
