from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from typer.testing import CliRunner

from revanent.application.report_command import ReportCommandResult
from revanent.application.workflows import (
    CancellationApplicationService,
    CancelRunRequest,
    CancelRunResult,
    ResumeApplicationService,
    ResumeRunRequest,
    ResumeRunResult,
    RunStatusRequest,
    RuntimeActionStatus,
    RuntimeComposition,
    StatusApplicationService,
    StatusComposition,
)
from revanent.cli import app as cli_app
from revanent.cli.app import app
from revanent.config import render_default_config
from revanent.domain import Run, RunState
from revanent.orchestration import OrchestrationService
from revanent.ports import CommandStatus, OrchestrationRequest, RuntimeBinding
from revanent.ports.git import GitRepository, RepositoryIdentity
from revanent.ports.reporting import (
    EvidenceReport,
    EvidenceReportStatus,
    ReproductionEvidence,
    VerificationEvidence,
)
from revanent.reporting import ReportRenderer
from revanent.storage import SQLiteRunRepository
from revanent.telemetry import TelemetryService
from tests.e2e.test_orchestration import (
    NOW,
    RUN_ID,
    _approved_review,
    _harness,
    _run,
)

runner = CliRunner()


def _report_result(
    status: EvidenceReportStatus = EvidenceReportStatus.COMPLETE,
) -> ReportCommandResult:
    report = EvidenceReport(
        report_id="report_" + "b" * 64,
        status=status,
        run_id=RUN_ID,
        generated_at=NOW,
        generator_version="0.1.0",
        evidence_complete=status is EvidenceReportStatus.COMPLETE,
        reproduction=ReproductionEvidence(
            configuration_schema_version=1,
            configuration_digest_sha256="c" * 64,
            platform="Windows",
            python_version="3.12.0",
        ),
        verification=VerificationEvidence(
            approval_gate_present=False,
            approval_permitted=False,
        ),
    )
    return ReportCommandResult(
        status=status,
        report=report,
        content=ReportRenderer().json(report).decode("utf-8"),
    )


def _initialized_source(source: Path) -> Path:
    (source / "revanent.yaml").write_bytes(render_default_config("runtime-fixture"))
    task = source / "task.json"
    task.write_text(_run().task.model_dump_json(), encoding="utf-8")
    return task


def _runtime_composition(
    service: OrchestrationService,
    request: OrchestrationRequest,
    repository: SQLiteRunRepository,
    git: GitRepository,
) -> RuntimeComposition:
    source = request.worktree.source_path

    def make_request(run: Run, revision: int | None) -> OrchestrationRequest:
        del run
        data = request.model_dump(mode="python")
        data["expected_revision"] = revision
        return OrchestrationRequest.model_validate(data)

    def make_binding(run: Run, identity: RepositoryIdentity) -> RuntimeBinding:
        return RuntimeBinding(
            run_id=run.id,
            repository=identity,
            worktree_id=request.worktree.worktree_id,
            worktree_relative_path=request.worktree.target_path.relative_to(source).as_posix(),
            branch_name=request.worktree.branch_name,
            created_at=run.created_at,
        )

    return RuntimeComposition(
        runs=repository,
        telemetry=TelemetryService(repository),
        orchestration=service,
        git=git,
        repository_root=source,
        make_run=lambda start: _run(),
        make_binding=make_binding,
        make_request=make_request,
    )


def test_fake_runtime_cli_run_status_resume_and_cancel_are_canonical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime space Ω"
    runtime_root.mkdir()
    service, request, repository, git, builder, reviewer, *_ = _harness(
        runtime_root,
        validation_statuses=(CommandStatus.SUCCESS,),
        review_results=(_approved_review(),),
        persist_run=False,
    )
    source = request.worktree.source_path
    task = _initialized_source(source)
    runtime = _runtime_composition(service, request, repository, git)
    status = StatusComposition(
        runs=repository,
        telemetry=TelemetryService(repository),
        git=git,
        repository_root=source,
    )
    monkeypatch.setattr(cli_app, "compose_runtime", lambda effective, **kwargs: runtime)
    monkeypatch.setattr(cli_app, "compose_status", lambda effective: status)
    monkeypatch.chdir(tmp_path)

    started = runner.invoke(
        app,
        ["run", "--repository", str(source), "--task-file", task.name, "--json"],
    )
    projected = runner.invoke(app, ["status", RUN_ID.root, "--repository", str(source), "--json"])
    resumed = runner.invoke(app, ["resume", RUN_ID.root, "--repository", str(source), "--json"])
    cancelled = runner.invoke(app, ["cancel", RUN_ID.root, "--repository", str(source), "--json"])

    assert started.exit_code == projected.exit_code == resumed.exit_code == cancelled.exit_code == 0
    start_payload = json.loads(started.stdout)
    status_payload = json.loads(projected.stdout)
    assert start_payload["result"]["run_id"] == RUN_ID.root
    assert start_payload["result"]["state"] == "APPROVED"
    assert status_payload["result"]["state"] == "APPROVED"
    assert status_payload["result"]["review"]["approval_gate_present"] is True
    assert builder.invocation_count == 1
    assert reviewer.invocation_count == 1
    assert _run().task.objective not in started.stdout
    assert _run().task.objective not in projected.stdout


def test_report_cli_is_json_canonical_and_has_expected_failure_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "repository space Ω"
    source.mkdir()
    _initialized_source(source)

    class FakeReportCommand:
        def generate(self, request: object) -> ReportCommandResult:
            del request
            return _report_result()

    monkeypatch.setattr(cli_app, "compose_report_command", lambda effective: FakeReportCommand())
    emitted = runner.invoke(app, ["report", RUN_ID.root, "--repository", str(source), "--json"])
    conflict = runner.invoke(
        app,
        ["report", RUN_ID.root, "--repository", str(source), "--json", "--format", "markdown"],
    )

    assert emitted.exit_code == 0
    assert json.loads(emitted.stdout)["run_id"] == RUN_ID.root
    assert conflict.exit_code == 2
    assert runner.invoke(app, ["cleanup"]).exit_code == 2


def test_concurrent_resumes_launch_each_side_effect_at_most_once(tmp_path: Path) -> None:
    service, request, repository, git, builder, reviewer, *_, commands = _harness(
        tmp_path,
        validation_statuses=(CommandStatus.SUCCESS,),
        review_results=(_approved_review(),),
        bind_runtime=True,
    )
    composition = _runtime_composition(service, request, repository, git)
    barrier = Barrier(2)

    def resume(_: int) -> ResumeRunResult:
        barrier.wait()
        return ResumeApplicationService(composition).resume(ResumeRunRequest(run_id=RUN_ID))

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(resume, range(2)))

    assert repository.get_run(RUN_ID).run.state is RunState.APPROVED
    assert {result.action_status for result in results} <= {
        RuntimeActionStatus.COMPLETED,
        RuntimeActionStatus.STALE,
    }
    assert builder.invocation_count == reviewer.invocation_count == 1
    assert len(commands.requests) == 1


def test_resume_cancel_race_is_terminal_and_never_duplicates_work(tmp_path: Path) -> None:
    service, request, repository, git, builder, reviewer, *_, commands = _harness(
        tmp_path,
        validation_statuses=(CommandStatus.SUCCESS,),
        review_results=(_approved_review(),),
        bind_runtime=True,
    )
    composition = _runtime_composition(service, request, repository, git)
    barrier = Barrier(2)

    def resume() -> ResumeRunResult:
        barrier.wait()
        return ResumeApplicationService(composition).resume(ResumeRunRequest(run_id=RUN_ID))

    def cancel() -> CancelRunResult:
        barrier.wait()
        return CancellationApplicationService(composition).cancel(CancelRunRequest(run_id=RUN_ID))

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (executor.submit(resume), executor.submit(cancel))
        results = tuple(future.result() for future in futures)

    assert repository.get_run(RUN_ID).run.state in {RunState.APPROVED, RunState.CANCELLED}
    assert {result.action_status for result in results} <= {
        RuntimeActionStatus.COMPLETED,
        RuntimeActionStatus.STALE,
    }
    assert builder.invocation_count <= 1
    assert reviewer.invocation_count <= 1
    assert len(commands.requests) <= 1


def test_cancel_is_idempotent_and_prestart_cancel_launches_nothing(tmp_path: Path) -> None:
    service, request, repository, git, builder, reviewer, *_, commands = _harness(
        tmp_path,
        validation_statuses=(CommandStatus.SUCCESS,),
        review_results=(_approved_review(),),
        bind_runtime=True,
    )
    application = CancellationApplicationService(
        _runtime_composition(service, request, repository, git)
    )

    stale = application.cancel(CancelRunRequest(run_id=RUN_ID, expected_revision=1))
    first = application.cancel(CancelRunRequest(run_id=RUN_ID))
    second = application.cancel(CancelRunRequest(run_id=RUN_ID))
    projected = StatusApplicationService(
        StatusComposition(
            runs=repository,
            telemetry=TelemetryService(repository),
            git=git,
            repository_root=request.worktree.source_path,
        )
    ).status(RunStatusRequest(run_id=RUN_ID))

    assert stale.action_status is RuntimeActionStatus.STALE
    assert first.cancelled is second.cancelled is True
    assert first.state is second.state is RunState.CANCELLED
    assert projected.cancellation_requested is projected.cancellation_terminal is True
    assert builder.invocation_count == reviewer.invocation_count == 0
    assert git.create_count == len(commands.requests) == 0


def test_wrong_worktree_blocks_status_resume_and_cancel_without_writes(tmp_path: Path) -> None:
    service, request, repository, git, *_ = _harness(
        tmp_path,
        validation_statuses=(CommandStatus.SUCCESS,),
        review_results=(_approved_review(),),
        bind_runtime=True,
    )
    assert service.execute(request).status.value == "APPROVED"
    composition = _runtime_composition(service, request, repository, git)
    status_composition = StatusComposition(
        runs=repository,
        telemetry=TelemetryService(repository),
        git=git,
        repository_root=request.worktree.source_path,
    )
    before = (
        repository.get_run(RUN_ID),
        repository.list_events(RUN_ID),
        repository.list_orchestration_records(RUN_ID),
    )
    git.fail_verification = True

    results = (
        StatusApplicationService(status_composition).status(RunStatusRequest(run_id=RUN_ID)),
        ResumeApplicationService(composition).resume(ResumeRunRequest(run_id=RUN_ID)),
        CancellationApplicationService(composition).cancel(CancelRunRequest(run_id=RUN_ID)),
    )

    assert all(result.action_status is RuntimeActionStatus.BLOCKED for result in results)
    assert before == (
        repository.get_run(RUN_ID),
        repository.list_events(RUN_ID),
        repository.list_orchestration_records(RUN_ID),
    )


def test_wrong_repository_blocks_resume_and_cancel_before_orchestration(tmp_path: Path) -> None:
    service, request, repository, git, builder, reviewer, *_, commands = _harness(
        tmp_path,
        validation_statuses=(CommandStatus.SUCCESS,),
        review_results=(_approved_review(),),
        bind_runtime=True,
    )
    composition = _runtime_composition(service, request, repository, git)
    before = (
        repository.get_run(RUN_ID),
        repository.list_events(RUN_ID),
        repository.list_orchestration_records(RUN_ID),
    )
    identity_data = git.record.repository.model_dump(mode="python")
    identity_data["repository_id"] = "repo_" + "f" * 64
    record_data = git.record.model_dump(mode="python")
    record_data["repository"] = RepositoryIdentity.model_validate(identity_data)
    git.record = type(git.record).model_validate(record_data)

    resumed = ResumeApplicationService(composition).resume(ResumeRunRequest(run_id=RUN_ID))
    cancelled = CancellationApplicationService(composition).cancel(CancelRunRequest(run_id=RUN_ID))

    assert resumed.action_status is cancelled.action_status is RuntimeActionStatus.BLOCKED
    assert resumed.failure is not None and resumed.failure.code == "repository_identity_mismatch"
    assert (
        cancelled.failure is not None and cancelled.failure.code == "repository_identity_mismatch"
    )
    assert before == (
        repository.get_run(RUN_ID),
        repository.list_events(RUN_ID),
        repository.list_orchestration_records(RUN_ID),
    )
    assert builder.invocation_count == reviewer.invocation_count == len(commands.requests) == 0
