"""Presentation-only Typer surface for safe P6 setup and inspection commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from revanent import __version__
from revanent.application import (
    ConfigurationService,
    DoctorService,
    InitializationService,
    ProviderDetectionService,
)
from revanent.application.doctor import DoctorResult
from revanent.application.initialization import InitializationResult
from revanent.application.provider_detection import ProviderCapability
from revanent.application.report_command import ReportCommandRequest
from revanent.application.runtime import RepositoryInspectionError
from revanent.application.runtime_composition import (
    RuntimeDependencyError,
    compose_report_command,
    compose_runtime,
    compose_status,
)
from revanent.application.task_input import TaskInputError, load_task_file
from revanent.application.workflows import (
    CancellationApplicationService,
    CancelRunRequest,
    ResumeApplicationService,
    ResumeRunRequest,
    RunApplicationService,
    RunStatusRequest,
    StartRunRequest,
    StatusApplicationService,
)
from revanent.config import ConfigurationError, load_effective_config
from revanent.domain import RunId, WorkPackage, WorkPackageId
from revanent.ports.reporting import EvidenceReportStatus, ReportFormat
from revanent.ports.storage import StorageError

EXIT_INVALID = 2
EXIT_CONFLICT = 3
EXIT_DEPENDENCY = 4
EXIT_NOT_FOUND = 5
EXIT_BLOCKED = 6
EXIT_STALE = 7
EXIT_FAILED = 8
EXIT_INTERNAL = 70

app = typer.Typer(
    name="revanent",
    help="Local-first software-engineering orchestration setup and inspection.",
    no_args_is_help=True,
)
config_app = typer.Typer(help="Read-only project configuration commands.", no_args_is_help=True)
agents_app = typer.Typer(help="Read-only provider capability commands.", no_args_is_help=True)
app.add_typer(config_app, name="config")
app.add_typer(agents_app, name="agents")
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
    """Run Revanent setup and environment inspection commands."""


@app.command()
def init(
    repository: Annotated[
        Path, typer.Option("--repository", help="Target repository directory.")
    ] = Path("."),
) -> None:
    """Create only the validated, missing Revanent configuration and owned directories."""
    result = InitializationService().initialize(repository)
    _render_initialization(result)
    if not result.succeeded:
        raise typer.Exit(code=EXIT_CONFLICT)


@app.command()
def doctor(
    repository: Annotated[
        Path | None,
        typer.Option("--repository", help="Optional repository to inspect without changing it."),
    ] = None,
    strict: Annotated[
        bool,
        typer.Option(help="Treat unavailable configured provider capabilities as failures."),
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Emit stable JSON to stdout.")] = False,
) -> None:
    """Report runtime, optional repository, configuration, and provider readiness read-only."""
    result = DoctorService().run(repository_path=repository, strict=strict)
    _render_doctor(result, as_json=as_json)
    if result.exit_code:
        raise typer.Exit(code=result.exit_code)


@app.command()
def run(
    repository: Annotated[
        Path, typer.Option("--repository", help="Initialized target repository.")
    ],
    task_file: Annotated[Path, typer.Option("--task-file", help="Repository-relative task JSON.")],
    work_package: Annotated[
        str, typer.Option("--work-package", help="Bounded work-package ID.")
    ] = "P6-002",
    as_json: Annotated[bool, typer.Option("--json", help="Emit stable JSON to stdout.")] = False,
) -> None:
    """Persist one new run before handing it to the durable coordinator."""
    try:
        effective = load_effective_config(repository)
        task = load_task_file(effective.repository_root, task_file)
        package = WorkPackage(
            id=WorkPackageId(work_package), title=work_package, objective=task.objective
        )
        result = RunApplicationService(compose_runtime(effective)).start(
            StartRunRequest(task=task, work_package=package)
        )
    except RuntimeDependencyError:
        raise typer.Exit(code=EXIT_DEPENDENCY) from None
    except (
        ConfigurationError,
        RepositoryInspectionError,
        StorageError,
        TaskInputError,
        ValueError,
        OSError,
    ):
        raise typer.Exit(code=EXIT_INVALID) from None
    _render_runtime("run", result, as_json)
    _raise_runtime_exit(result)


@app.command()
def resume(
    run_id: Annotated[str, typer.Argument(help="Canonical run ID.")],
    repository: Annotated[
        Path, typer.Option("--repository", help="Initialized target repository.")
    ],
    expected_revision: Annotated[int | None, typer.Option("--expected-revision", min=0)] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Emit stable JSON to stdout.")] = False,
) -> None:
    """Reconcile and continue exactly one existing run from durable evidence."""
    try:
        effective = load_effective_config(repository)
        result = ResumeApplicationService(compose_runtime(effective)).resume(
            ResumeRunRequest(run_id=RunId(run_id), expected_revision=expected_revision)
        )
    except RuntimeDependencyError:
        raise typer.Exit(code=EXIT_DEPENDENCY) from None
    except (ConfigurationError, RepositoryInspectionError, StorageError, ValueError, OSError):
        raise typer.Exit(code=EXIT_INVALID) from None
    _render_runtime("resume", result, as_json)
    _raise_runtime_exit(result)


@app.command()
def status(
    run_id: Annotated[str, typer.Argument(help="Canonical run ID.")],
    repository: Annotated[
        Path, typer.Option("--repository", help="Initialized target repository.")
    ],
    as_json: Annotated[bool, typer.Option("--json", help="Emit stable JSON to stdout.")] = False,
) -> None:
    """Read durable run evidence only; status neither reconciles nor executes work."""
    try:
        effective = load_effective_config(repository)
        result = StatusApplicationService(compose_status(effective)).status(
            RunStatusRequest(run_id=RunId(run_id))
        )
    except (ConfigurationError, RepositoryInspectionError, StorageError, ValueError, OSError):
        raise typer.Exit(code=EXIT_INVALID) from None
    _render_runtime("status", result, as_json)
    _raise_runtime_exit(result)


@app.command()
def cancel(
    run_id: Annotated[str, typer.Argument(help="Canonical run ID.")],
    repository: Annotated[
        Path, typer.Option("--repository", help="Initialized target repository.")
    ],
    expected_revision: Annotated[int | None, typer.Option("--expected-revision", min=0)] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Emit stable JSON to stdout.")] = False,
) -> None:
    """Durably cancel at a safe boundary without cleanup or evidence deletion."""
    try:
        effective = load_effective_config(repository)
        result = CancellationApplicationService(
            compose_runtime(effective, require_providers=False)
        ).cancel(CancelRunRequest(run_id=RunId(run_id), expected_revision=expected_revision))
    except RuntimeDependencyError:
        raise typer.Exit(code=EXIT_DEPENDENCY) from None
    except (ConfigurationError, RepositoryInspectionError, StorageError, ValueError, OSError):
        raise typer.Exit(code=EXIT_INVALID) from None
    _render_runtime("cancel", result, as_json)
    _raise_runtime_exit(result)


@app.command()
def report(
    run_id: Annotated[str, typer.Argument(help="Canonical run ID.")],
    repository: Annotated[
        Path, typer.Option("--repository", help="Initialized target repository.")
    ],
    format_: Annotated[
        ReportFormat | None,
        typer.Option("--format", help="Rendered report format: json or markdown."),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output", help="Report-root-relative output path; omitted means stdout only."
        ),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Alias for --format json; cannot be combined with markdown."),
    ] = False,
) -> None:
    """Generate one read-only durable evidence report; never reconciles or invokes providers."""
    if as_json and format_ is ReportFormat.MARKDOWN:
        raise typer.Exit(code=EXIT_INVALID)
    selected_format = ReportFormat.JSON if as_json else (format_ or ReportFormat.MARKDOWN)
    try:
        effective = load_effective_config(repository)
        result = compose_report_command(effective).generate(
            ReportCommandRequest(
                run_id=run_id,
                format=selected_format,
                output=str(output) if output is not None else None,
            )
        )
    except (ConfigurationError, RepositoryInspectionError, StorageError, ValueError, OSError):
        raise typer.Exit(code=EXIT_INVALID) from None
    typer.echo(result.content, nl=False)
    _raise_report_exit(result.status)


@config_app.command("validate")
def config_validate(
    repository: Annotated[
        Path, typer.Option("--repository", help="Target repository directory.")
    ] = Path("."),
    config_path: Annotated[
        Path | None,
        typer.Option("--config", help="Only the repository-root revanent.yaml path is accepted."),
    ] = None,
    max_total_minutes: Annotated[
        int | None,
        typer.Option(
            "--max-total-minutes", min=1, max=10_080, help="Reviewed temporary budget override."
        ),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Emit stable JSON to stdout.")] = False,
) -> None:
    """Validate the effective schema-v1 project configuration without creating files."""
    result = ConfigurationService().validate(
        repository,
        config_path=config_path,
        max_total_minutes=max_total_minutes,
    )
    payload = {
        "schema_version": 1,
        "command": "config.validate",
        "valid": result.valid,
        "code": result.code,
        "message": result.message,
    }
    if result.effective is not None:
        payload["configuration"] = {
            "path": result.effective.path.name,
            "max_total_minutes_source": result.effective.max_total_minutes_source.value,
        }
    if as_json:
        _json(payload)
    elif result.valid:
        console.print("Configuration is valid.")
    else:
        console.print(f"Configuration is invalid: {result.message}")
    if not result.valid:
        raise typer.Exit(code=EXIT_INVALID)


@agents_app.command("detect")
def agents_detect(
    repository: Annotated[
        Path, typer.Option("--repository", help="Repository used only as a safe probe cwd.")
    ] = Path("."),
    strict: Annotated[
        bool,
        typer.Option(help="Fail when a supported provider surface is unavailable or incompatible."),
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Emit stable JSON to stdout.")] = False,
) -> None:
    """Detect supported provider capabilities with version/help probes only."""
    root = repository.resolve(strict=False)
    capabilities = ProviderDetectionService().detect(root)
    if as_json:
        _json(
            {
                "schema_version": 1,
                "command": "agents.detect",
                "providers": [_provider_payload(capability) for capability in capabilities],
            }
        )
    else:
        _render_providers(capabilities)
    if strict and any(capability.status.value != "AVAILABLE" for capability in capabilities):
        raise typer.Exit(code=EXIT_DEPENDENCY)


def _render_initialization(result: InitializationResult) -> None:
    if result.plan is not None:
        table = Table(title="Revanent initialization")
        table.add_column("Path")
        table.add_column("Action")
        table.add_column("Detail")
        for resource in result.plan.resources:
            table.add_row(resource.relative_path, resource.action.value, resource.reason)
        console.print(table)
    console.print(result.message)


def _render_doctor(result: DoctorResult, *, as_json: bool) -> None:
    if as_json:
        _json(
            {
                "schema_version": 1,
                "command": "doctor",
                "checks": [
                    {
                        "name": check.name,
                        "status": check.status.value,
                        "required": check.required,
                        "detail": check.detail,
                    }
                    for check in result.checks
                ],
            }
        )
        return
    table = Table(title="Revanent environment")
    table.add_column("Capability")
    table.add_column("Status")
    table.add_column("Detail")
    for check in result.checks:
        table.add_row(check.name, check.status.value, check.detail)
    console.print(table)


def _render_providers(capabilities: tuple[ProviderCapability, ...]) -> None:
    table = Table(title="Revanent provider capabilities")
    table.add_column("Provider")
    table.add_column("Status")
    table.add_column("Roles")
    table.add_column("Detail")
    for capability in capabilities:
        roles = (
            ", ".join(
                role
                for role, enabled in (
                    ("builder", capability.builder),
                    ("review", capability.review),
                    ("repair", capability.repair),
                )
                if enabled
            )
            or "none"
        )
        table.add_row(
            capability.provider,
            capability.status.value,
            roles,
            capability.version or capability.reason_code,
        )


def _provider_payload(capability: ProviderCapability) -> dict[str, object]:
    return {
        "provider": capability.provider,
        "status": capability.status.value,
        "version": capability.version,
        "roles": {
            "builder": capability.builder,
            "review": capability.review,
            "repair": capability.repair,
        },
        "reason_code": capability.reason_code,
    }


def _json(payload: dict[str, object]) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _render_runtime(command: str, result: object, as_json: bool) -> None:
    payload = result.model_dump(mode="json")  # type: ignore[attr-defined]
    if as_json:
        _json({"schema_version": 1, "command": command, "result": payload})
        return
    console.print(f"{command}: {payload['action_status']}")
    if payload.get("run_id"):
        console.print(f"run: {payload['run_id']}  state: {payload.get('state') or 'UNKNOWN'}")
    console.print(payload["reason"])
    console.print(f"next: {payload['next_action']}")


def _raise_runtime_exit(result: object) -> None:
    status = result.action_status.value  # type: ignore[attr-defined]
    codes = {
        "COMPLETED": 0,
        "NOT_FOUND": EXIT_NOT_FOUND,
        "BLOCKED": EXIT_BLOCKED,
        "STALE": EXIT_STALE,
        "FAILED": EXIT_FAILED,
        "INVALID_EVIDENCE": EXIT_FAILED,
    }
    code = codes.get(status, EXIT_INTERNAL)
    failure = getattr(result, "failure", None)
    if status == "FAILED" and failure is not None and failure.kind.value == "INTERNAL":
        code = EXIT_INTERNAL
    if code:
        raise typer.Exit(code=code)


def _raise_report_exit(status: EvidenceReportStatus) -> None:
    code = {
        EvidenceReportStatus.COMPLETE: 0,
        EvidenceReportStatus.COMPLETE_WITH_WARNINGS: 0,
        EvidenceReportStatus.INCOMPLETE: EXIT_BLOCKED,
        EvidenceReportStatus.INVALID_EVIDENCE: EXIT_FAILED,
        EvidenceReportStatus.BLOCKED: EXIT_BLOCKED,
        EvidenceReportStatus.NOT_FOUND: EXIT_NOT_FOUND,
        EvidenceReportStatus.OUTPUT_CONFLICT: EXIT_CONFLICT,
        EvidenceReportStatus.INTERNAL_FAILURE: EXIT_INTERNAL,
    }[status]
    if code:
        raise typer.Exit(code=code)
