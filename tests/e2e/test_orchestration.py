from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from revanent.agents import (
    FakeAgentAdapter,
    FakeAgentScenario,
    FakeAgentStep,
    ScriptedResponseOutcome,
    agent_request_digest,
)
from revanent.commands import CancellationSource
from revanent.domain import (
    AttemptCounters,
    BudgetLimits,
    EventId,
    FindingSeverity,
    ReviewFinding,
    ReviewResult,
    ReviewVerdict,
    Run,
    RunId,
    RunState,
    TaskId,
    TaskSpecification,
    TransitionResult,
    WorkPackage,
    WorkPackageId,
)
from revanent.orchestration import (
    DeterministicOrchestrationIds,
    OrchestrationAdapters,
    OrchestrationService,
)
from revanent.ports import (
    AgentFailure,
    AgentFailureCategory,
    AgentRequest,
    AgentResponse,
    AgentRole,
    AgentStatus,
    BuilderPayload,
    CancellationToken,
    CapturedOutput,
    CommandFailure,
    CommandFailureCategory,
    CommandRequest,
    CommandResult,
    CommandStatus,
    ConcurrentRunUpdateError,
    DuplicateEventError,
    GitError,
    GitErrorCategory,
    GitOperationStatus,
    LimitOutcome,
    OrchestrationRequest,
    OrchestrationStatus,
    OrchestrationStep,
    RepairerPayload,
    RepositoryIdentity,
    RepositorySnapshot,
    RepositoryStatus,
    RetryDisposition,
    ReviewerPayload,
    ScenarioId,
    SideEffectState,
    StorageOperationError,
    StoredRun,
    StructuredParseStatus,
    ValidationArtifactPolicy,
    ValidationCommand,
    ValidationCommandClass,
    ValidationCommandId,
    ValidationExecutionPolicy,
    ValidationPlan,
    ValidationPlanId,
    ValidationPlanResult,
    WorkspaceKind,
    WorkspaceReference,
    WorktreeCleanupResult,
    WorktreeCreationRequest,
    WorktreeCreationResult,
    WorktreeId,
    WorktreeLifecycleStatus,
    WorktreeOwnershipRecord,
    WorktreeSnapshot,
    WorktreeVerificationResult,
)
from revanent.review import LocalApprovalEvidence, ReviewGate
from revanent.storage import SQLiteRunRepository
from revanent.validation import ValidationRunner
from tests.agent_factories import make_capabilities, make_request

NOW = datetime(2026, 7, 30, 12, tzinfo=UTC)
RUN_ID = RunId(f"run_{'a' * 32}")
WORK_PACKAGE_ID = WorkPackageId("P4-002")
COMMIT = "1" * 40


@dataclass
class DeterministicClock:
    current: datetime = NOW

    def now(self) -> datetime:
        self.current += timedelta(milliseconds=100)
        return self.current


@dataclass
class CancelAfterChecks:
    cancel_on: int
    checks: int = 0

    def is_cancelled(self) -> bool:
        self.checks += 1
        return self.checks >= self.cancel_on


@dataclass
class ScriptedCommandRunner:
    statuses: tuple[CommandStatus, ...]
    requests: list[CommandRequest] = field(default_factory=list)

    def run(self, request: CommandRequest) -> CommandResult:
        index = len(self.requests)
        self.requests.append(request)
        status = self.statuses[index]
        started = NOW + timedelta(seconds=100 + index * 2)
        failure = None
        exit_code: int | None = 0
        if status is CommandStatus.NONZERO_EXIT:
            exit_code = 7
        elif status is not CommandStatus.SUCCESS:
            exit_code = None
            failure = CommandFailure(
                category=(
                    CommandFailureCategory.EXECUTABLE_UNAVAILABLE
                    if status is CommandStatus.POLICY_REJECTED
                    else CommandFailureCategory.LAUNCH
                ),
                message="bounded fake validation failure",
            )
        empty = CapturedOutput(text="", observed_bytes=0, retained_bytes=0, truncated=False)
        return CommandResult(
            correlation_id=request.correlation_id,
            executable=request.executable,
            resolved_executable=Path(__file__).resolve(),
            status=status,
            started_at=started,
            completed_at=started + timedelta(seconds=1),
            duration_seconds=1.0,
            stdout=empty,
            stderr=empty,
            exit_code=exit_code,
            failure=failure,
        )


class FakeGitRepository:
    def __init__(self, source: Path, target: Path, worktree_id: WorktreeId) -> None:
        repository_id = "repo_" + "2" * 64
        identity = RepositoryIdentity(
            repository_id=repository_id,
            worktree_root=source,
            git_directory=source / ".git",
            common_git_directory=source / ".git",
            object_format="sha1",
            root_commits=(COMMIT,),
        )
        self.record = WorktreeOwnershipRecord(
            worktree_id=worktree_id,
            run_id=RUN_ID.root,
            repository=identity,
            worktree_path=target,
            branch_name="revanent/P4-002",
            base_commit=COMMIT,
            created_head=COMMIT,
            created_at=NOW,
            revanent_version="0.1.0",
            lifecycle_status=WorktreeLifecycleStatus.ACTIVE,
        )
        self.worktree = WorktreeSnapshot(
            path=target,
            head_commit=COMMIT,
            branch="revanent/P4-002",
        )
        self.repository = RepositorySnapshot(
            identity=identity,
            branch="revanent/P4-002",
            detached_head=False,
            head_commit=COMMIT,
            status=RepositoryStatus(),
            worktrees=(self.worktree,),
        )
        self.create_count = 0
        self.verify_count = 0
        self.fail_verification = False

    def discover(self, path: Path) -> RepositoryIdentity:
        return self.record.repository

    def inspect(self, path: Path) -> RepositorySnapshot:
        return self.repository

    def create_worktree(self, request: WorktreeCreationRequest) -> WorktreeCreationResult:
        self.create_count += 1
        return WorktreeCreationResult(
            status=GitOperationStatus.CREATED,
            record=self.record,
            worktree=self.worktree,
        )

    def verify_owned_worktree(self, worktree_id: WorktreeId) -> WorktreeVerificationResult:
        self.verify_count += 1
        if self.fail_verification or worktree_id != self.record.worktree_id:
            raise GitError("fake ownership mismatch")
        return WorktreeVerificationResult(
            status=GitOperationStatus.VERIFIED,
            record=self.record,
            worktree=self.worktree,
            repository=self.repository,
        )

    def cleanup_worktree(self, worktree_id: WorktreeId) -> WorktreeCleanupResult:
        raise AssertionError("orchestration must never clean worktrees")


@dataclass
class EvidenceCollector:
    scope_justified: bool = True

    def collect(
        self,
        *,
        validation_plan: ValidationPlan,
        validation_result: ValidationPlanResult,
        reviewer_response: AgentResponse,
        observed_at: datetime,
    ) -> LocalApprovalEvidence:
        return LocalApprovalEvidence(
            run_id=validation_plan.run_id,
            work_package_id=validation_plan.work_package_id,
            validation_plan_id=validation_plan.id,
            review_invocation_id=reviewer_response.invocation_id,
            review_adapter_id=reviewer_response.identity.adapter_id,
            observed_at=observed_at,
            scope_justified=self.scope_justified,
            generated_files_consistent=True,
            lockfiles_consistent=True,
            evidence_complete=True,
            required_artifacts_complete=True,
            repository_clean=True,
            review_read_only_verified=True,
            side_effects_reconciled=True,
        )


def _run(
    *, repairs: int = 2, builds: int = 2, reviews: int = 3, duration_seconds: int = 3_600
) -> Run:
    return Run(
        id=RUN_ID,
        task=TaskSpecification(
            id=TaskId(f"task_{'b' * 32}"),
            objective="Exercise bounded orchestration.",
            allowed_paths=("src/**", "tests/**"),
            forbidden_paths=(".git/**",),
            acceptance_criteria=("All durable gates pass.",),
        ),
        work_package=WorkPackage(
            id=WORK_PACKAGE_ID,
            title="Bounded orchestration",
            objective="Prove fake-first end-to-end behavior.",
        ),
        budgets=BudgetLimits(
            max_duration_seconds=duration_seconds,
            max_build_attempts=builds,
            max_review_attempts=reviews,
            max_repair_attempts=repairs,
        ),
        created_at=NOW,
        updated_at=NOW,
    )


def _prototype(role: AgentRole, target: Path, worktree_id: WorktreeId) -> AgentRequest:
    request = make_request(role)
    values = request.model_dump(mode="python")
    values.update(
        run_id=RUN_ID,
        work_package_id=WORK_PACKAGE_ID,
        workspace=WorkspaceReference(
            kind=WorkspaceKind.WORKTREE,
            reference_id=worktree_id.root,
            root=target,
        ),
    )
    return AgentRequest.model_validate(values)


def _invocation(prototype: AgentRequest, step: OrchestrationStep, sequence: int) -> AgentRequest:
    ids = DeterministicOrchestrationIds()
    attempt_id = ids.attempt_id(RUN_ID, step, sequence)
    values = prototype.model_dump(mode="python")
    values.update(
        attempt_id=ids.agent_attempt_id(attempt_id),
        invocation_id=ids.invocation_id(attempt_id),
        attempt_number=sequence,
    )
    return AgentRequest.model_validate(values)


def _adapter(
    role: AgentRole,
    prototype: AgentRequest,
    *,
    step: OrchestrationStep,
    payloads: tuple[BuilderPayload | ReviewerPayload | RepairerPayload, ...],
    cancellation_checkpoints: int = 0,
    sequence_start: int = 1,
) -> FakeAgentAdapter:
    requests = tuple(
        _invocation(prototype, step, sequence)
        for sequence in range(sequence_start, sequence_start + len(payloads))
    )
    steps = tuple(
        FakeAgentStep(
            expected_request_sha256=agent_request_digest(request),
            started_at=NOW + timedelta(seconds=200 + sequence * 10),
            duration_ms=1_000,
            cancellation_checkpoints=cancellation_checkpoints,
            outcome=ScriptedResponseOutcome(
                status=AgentStatus.COMPLETED,
                summary="bounded fake completion",
                structured_parse_status=StructuredParseStatus.PARSED,
                payload=payload,
            ),
        )
        for sequence, (request, payload) in enumerate(
            zip(requests, payloads, strict=True), start=sequence_start
        )
    )
    return FakeAgentAdapter(
        FakeAgentScenario(
            scenario_id=ScenarioId(f"scenario.{role.value.lower()}.{step.value.lower()}"),
            capabilities=make_capabilities(roles=(role,), writes=role is not AgentRole.REVIEWER),
            default_timestamp=NOW,
            steps=steps,
        )
    )


def _failing_adapter(
    role: AgentRole,
    prototype: AgentRequest,
    *,
    step: OrchestrationStep,
    status: AgentStatus,
    failure: AgentFailure,
) -> FakeAgentAdapter:
    request = _invocation(prototype, step, 1)
    return FakeAgentAdapter(
        FakeAgentScenario(
            scenario_id=ScenarioId(f"scenario.{role.value.lower()}.{step.value.lower()}.failure"),
            capabilities=make_capabilities(roles=(role,), writes=role is not AgentRole.REVIEWER),
            default_timestamp=NOW,
            steps=(
                FakeAgentStep(
                    expected_request_sha256=agent_request_digest(request),
                    started_at=NOW + timedelta(seconds=210),
                    duration_ms=1_000,
                    outcome=ScriptedResponseOutcome(
                        status=status,
                        summary="bounded fake failure",
                        structured_parse_status=StructuredParseStatus.FAILED,
                        failure=failure,
                    ),
                ),
            ),
        )
    )


def _plan(target: Path, worktree_id: WorktreeId, sequence: int) -> ValidationPlan:
    return ValidationPlan(
        id=ValidationPlanId(f"vplan_{sequence:032x}"),
        run_id=RUN_ID,
        work_package_id=WORK_PACKAGE_ID,
        created_at=NOW,
        workspace=WorkspaceReference(
            kind=WorkspaceKind.WORKTREE,
            reference_id=worktree_id.root,
            root=target,
        ),
        commands=(
            ValidationCommand(
                id=ValidationCommandId("vcmd_tests"),
                name="tests",
                executable="fixture-python",
                arguments=("fixture", "exit", "0"),
                classification=ValidationCommandClass.REQUIRED,
            ),
        ),
        execution=ValidationExecutionPolicy(),
        artifacts=ValidationArtifactPolicy(
            root_id="validation-artifacts.fixture", allow_artifacts=False
        ),
    )


def _harness(
    tmp_path: Path,
    *,
    validation_statuses: tuple[CommandStatus, ...],
    review_results: tuple[ReviewResult, ...],
    local_repairs: int = 0,
    codex_repairs: int = 0,
    codex_authorized: bool = False,
    builder_failure: tuple[AgentStatus, AgentFailure] | None = None,
    reviewer_failure: tuple[AgentStatus, AgentFailure] | None = None,
    builder_cancellation_checkpoints: int = 0,
    codex_sequence_start: int = 1,
    scope_justified: bool = True,
    clock_start: datetime = NOW,
    run: Run | None = None,
) -> tuple[
    OrchestrationService,
    OrchestrationRequest,
    SQLiteRunRepository,
    FakeGitRepository,
    FakeAgentAdapter,
    FakeAgentAdapter,
    FakeAgentAdapter,
    FakeAgentAdapter | None,
    ScriptedCommandRunner,
]:
    source = (tmp_path / "source").resolve()
    target = (tmp_path / "owned-worktree").resolve()
    source.mkdir()
    target.mkdir()
    worktree_id = WorktreeId(f"wt_{'c' * 32}")
    worktree = WorktreeCreationRequest(
        source_path=source,
        target_path=target,
        worktree_id=worktree_id,
        branch_name="revanent/P4-002",
        run_id=RUN_ID.root,
    )
    builder_request = _prototype(AgentRole.BUILDER, target, worktree_id)
    reviewer_request = _prototype(AgentRole.REVIEWER, target, worktree_id)
    local_request = _prototype(AgentRole.BUILDER, target, worktree_id)
    codex_request = _prototype(AgentRole.REPAIRER, target, worktree_id)
    builder = (
        _adapter(
            AgentRole.BUILDER,
            builder_request,
            step=OrchestrationStep.BUILD,
            payloads=(
                BuilderPayload(
                    implementation_summary="implemented bounded task",
                    files_inspected=(),
                    files_claimed_changed=(),
                ),
            ),
            cancellation_checkpoints=builder_cancellation_checkpoints,
        )
        if builder_failure is None
        else _failing_adapter(
            AgentRole.BUILDER,
            builder_request,
            step=OrchestrationStep.BUILD,
            status=builder_failure[0],
            failure=builder_failure[1],
        )
    )
    reviewer = (
        _adapter(
            AgentRole.REVIEWER,
            reviewer_request,
            step=OrchestrationStep.REVIEW,
            payloads=tuple(
                ReviewerPayload(review=result, files_inspected=()) for result in review_results
            ),
        )
        if reviewer_failure is None
        else _failing_adapter(
            AgentRole.REVIEWER,
            reviewer_request,
            step=OrchestrationStep.REVIEW,
            status=reviewer_failure[0],
            failure=reviewer_failure[1],
        )
    )
    local_repair = _adapter(
        AgentRole.BUILDER,
        local_request,
        step=OrchestrationStep.REPAIR,
        payloads=tuple(
            BuilderPayload(
                implementation_summary=f"local repair {index}",
                files_inspected=(),
                files_claimed_changed=(),
            )
            for index in range(1, local_repairs + 1)
        ),
    )
    codex = (
        _adapter(
            AgentRole.REPAIRER,
            codex_request,
            step=OrchestrationStep.REPAIR,
            payloads=tuple(
                RepairerPayload(
                    repair_summary=f"Codex repair {index}",
                    files_inspected=(),
                    files_claimed_changed=(),
                )
                for index in range(1, codex_repairs + 1)
            ),
            sequence_start=codex_sequence_start,
        )
        if codex_repairs
        else None
    )
    plans = tuple(_plan(target, worktree_id, index) for index in range(1, 8))
    request = OrchestrationRequest(
        run_id=RUN_ID,
        expected_revision=0,
        worktree=worktree,
        builder_request=builder_request,
        reviewer_request=reviewer_request,
        local_repair_request=local_request,
        codex_repair_request=codex_request if codex is not None else None,
        validation_plans=plans,
        codex_repair_authorized=codex_authorized,
    )
    repository = SQLiteRunRepository(tmp_path / "state.db")
    repository.initialize()
    repository.create_run(run or _run())
    git = FakeGitRepository(source, target, worktree_id)
    commands = ScriptedCommandRunner(validation_statuses)
    service = OrchestrationService(
        runs=repository,
        journal=repository,
        git=git,
        adapters=OrchestrationAdapters(
            builder=builder,
            reviewer=reviewer,
            local_repair=local_repair,
            codex_repair=codex,
        ),
        validation=ValidationRunner(commands),
        review_gate=ReviewGate(),
        local_evidence=EvidenceCollector(scope_justified=scope_justified),
        clock=DeterministicClock(current=clock_start),
    )
    return (
        service,
        request,
        repository,
        git,
        builder,
        reviewer,
        local_repair,
        codex,
        commands,
    )


def _approved_review() -> ReviewResult:
    return ReviewResult(
        verdict=ReviewVerdict.APPROVED,
        summary="Structured reviewer evidence approves the validated change.",
    )


def test_builder_validation_review_happy_path_reaches_approved(tmp_path: Path) -> None:
    service, request, repository, git, builder, reviewer, local, codex, commands = _harness(
        tmp_path,
        validation_statuses=(CommandStatus.SUCCESS,),
        review_results=(_approved_review(),),
    )

    result = service.execute(request)

    assert result.status is OrchestrationStatus.APPROVED
    assert result.run.state is RunState.APPROVED
    assert result.run.approval_gate is not None
    assert result.run.attempts.build == 1
    assert result.run.attempts.review == 1
    assert result.run.attempts.repair == 0
    assert git.create_count == 1
    assert builder.invocation_count == 1
    assert reviewer.invocation_count == 1
    assert local.invocation_count == 0
    assert codex is None
    assert len(commands.requests) == 1
    assert repository.get_run(RUN_ID).run == result.run

    replay = service.execute(request)
    assert replay.status is OrchestrationStatus.APPROVED
    assert builder.invocation_count == reviewer.invocation_count == 1
    assert len(commands.requests) == 1


def test_failed_validation_selects_one_local_repair_then_revalidates(
    tmp_path: Path,
) -> None:
    service, request, _, _, builder, reviewer, local, _, commands = _harness(
        tmp_path,
        validation_statuses=(CommandStatus.NONZERO_EXIT, CommandStatus.SUCCESS),
        review_results=(_approved_review(),),
        local_repairs=1,
    )

    result = service.execute(request)

    assert result.status is OrchestrationStatus.APPROVED
    assert result.run.attempts.build == 1
    assert result.run.attempts.repair == 1
    assert result.run.attempts.review == 1
    assert builder.invocation_count == 1
    assert local.invocation_count == 1
    assert reviewer.invocation_count == 1
    assert len(commands.requests) == 2


def test_high_risk_review_selects_authorized_codex_repair_and_rereview(
    tmp_path: Path,
) -> None:
    changes = ReviewResult(
        verdict=ReviewVerdict.CHANGES_REQUIRED,
        summary="Security-sensitive change requires a repair.",
        findings=(
            ReviewFinding(
                severity=FindingSeverity.HIGH,
                summary="Persistence boundary is not fail closed.",
            ),
        ),
    )
    service, request, _, _, _, reviewer, local, codex, commands = _harness(
        tmp_path,
        validation_statuses=(CommandStatus.SUCCESS, CommandStatus.SUCCESS),
        review_results=(changes, _approved_review()),
        codex_repairs=1,
        codex_authorized=True,
    )

    result = service.execute(request)

    assert result.status is OrchestrationStatus.APPROVED
    assert result.run.attempts.repair == 1
    assert result.run.attempts.review == 2
    assert local.invocation_count == 0
    assert codex is not None and codex.invocation_count == 1
    assert reviewer.invocation_count == 2
    assert len(commands.requests) == 2


def test_repeated_validation_defect_escalates_local_repair_to_codex(
    tmp_path: Path,
) -> None:
    service, request, _, _, _, reviewer, local, codex, commands = _harness(
        tmp_path,
        validation_statuses=(
            CommandStatus.NONZERO_EXIT,
            CommandStatus.NONZERO_EXIT,
            CommandStatus.SUCCESS,
        ),
        review_results=(_approved_review(),),
        local_repairs=1,
        codex_repairs=1,
        codex_authorized=True,
        codex_sequence_start=2,
    )

    result = service.execute(request)

    assert result.status is OrchestrationStatus.APPROVED
    assert result.run.attempts.repair == 2
    assert local.invocation_count == 1
    assert codex is not None and codex.invocation_count == 1
    assert reviewer.invocation_count == 1
    assert len(commands.requests) == 3
    decisions = [
        record.attempt.decision.strategy.value
        for record in result.records
        if record.attempt.kind is OrchestrationStep.REPAIR and record.stage.value == "INTENT"
    ]
    assert decisions == ["LOCAL_BUILDER", "CODEX_REPAIR"]


def test_stale_revision_and_prestart_cancellation_launch_nothing(tmp_path: Path) -> None:
    service, request, _, git, builder, reviewer, local, _, commands = _harness(
        tmp_path,
        validation_statuses=(CommandStatus.SUCCESS,),
        review_results=(_approved_review(),),
    )
    stale_values = request.model_dump(mode="python")
    stale_values["expected_revision"] = 1
    stale = service.execute(OrchestrationRequest.model_validate(stale_values))

    assert stale.status is OrchestrationStatus.STALE
    assert git.create_count == builder.invocation_count == reviewer.invocation_count == 0
    assert local.invocation_count == len(commands.requests) == 0


def test_concurrent_coordinators_share_one_durable_side_effect_boundary(
    tmp_path: Path,
) -> None:
    service, request, repository, git, builder, reviewer, local, codex, commands = _harness(
        tmp_path,
        validation_statuses=(CommandStatus.SUCCESS,),
        review_results=(_approved_review(),),
    )
    competing = OrchestrationService(
        runs=repository,
        journal=repository,
        git=git,
        adapters=OrchestrationAdapters(
            builder=builder,
            reviewer=reviewer,
            local_repair=local,
            codex_repair=codex,
        ),
        validation=ValidationRunner(commands),
        review_gate=ReviewGate(),
        local_evidence=EvidenceCollector(),
        clock=DeterministicClock(),
    )

    def coordinate(candidate: OrchestrationService) -> OrchestrationStatus:
        try:
            return candidate.execute(request).status
        except (ConcurrentRunUpdateError, DuplicateEventError):
            return OrchestrationStatus.STALE

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = tuple(executor.map(coordinate, (service, competing)))

    assert repository.get_run(RUN_ID).run.state is RunState.APPROVED
    assert all(
        status in {OrchestrationStatus.APPROVED, OrchestrationStatus.STALE} for status in statuses
    )
    assert git.create_count == builder.invocation_count == reviewer.invocation_count == 1
    assert len(commands.requests) == 1


def test_worktree_ownership_failure_blocks_before_builder(tmp_path: Path) -> None:
    service, request, _, git, builder, _, _, _, commands = _harness(
        tmp_path,
        validation_statuses=(CommandStatus.SUCCESS,),
        review_results=(_approved_review(),),
    )
    git.fail_verification = True

    result = service.execute(request)

    assert result.status is OrchestrationStatus.BLOCKED
    assert result.run.state is RunState.BLOCKED
    assert builder.invocation_count == 0
    assert len(commands.requests) == 0


def test_partial_worktree_ownership_blocks_before_builder(tmp_path: Path) -> None:
    service, request, _, git, builder, _, _, _, commands = _harness(
        tmp_path,
        validation_statuses=(CommandStatus.SUCCESS,),
        review_results=(_approved_review(),),
    )
    record_values = git.record.model_dump(mode="python")
    record_values.update(
        lifecycle_status=WorktreeLifecycleStatus.PARTIAL,
        last_error_category=GitErrorCategory.PARTIAL_CREATION,
    )
    git.record = WorktreeOwnershipRecord.model_validate(record_values)

    result = service.execute(request)

    assert result.status is OrchestrationStatus.BLOCKED
    assert builder.invocation_count == 0
    assert len(commands.requests) == 0


def test_prestart_cancellation_is_terminal_and_launches_nothing(tmp_path: Path) -> None:
    service, request, _, git, builder, reviewer, local, _, commands = _harness(
        tmp_path,
        validation_statuses=(CommandStatus.SUCCESS,),
        review_results=(_approved_review(),),
    )
    cancellation = CancellationSource()
    cancellation.cancel()

    result = service.execute(request, cancellation=cancellation)

    assert result.status is OrchestrationStatus.CANCELLED
    assert result.run.state is RunState.CANCELLED
    assert git.create_count == builder.invocation_count == reviewer.invocation_count == 0
    assert local.invocation_count == len(commands.requests) == 0
    assert service.execute(request).status is OrchestrationStatus.CANCELLED


def test_mid_build_cancellation_preserves_attempt_and_stops_pipeline(tmp_path: Path) -> None:
    service, request, _, _, builder, reviewer, local, _, commands = _harness(
        tmp_path,
        validation_statuses=(CommandStatus.SUCCESS,),
        review_results=(_approved_review(),),
        builder_cancellation_checkpoints=2,
    )
    cancellation = CancelAfterChecks(cancel_on=7)

    result = service.execute(request, cancellation=cancellation)

    assert result.status is OrchestrationStatus.CANCELLED
    assert builder.invocation_count == 1
    assert reviewer.invocation_count == local.invocation_count == 0
    assert len(commands.requests) == 0
    assert any(record.attempt.status.value == "CANCELLED" for record in result.records)


def test_inflight_reviewer_cancellation_reaches_cancelled_not_blocked(tmp_path: Path) -> None:
    service, request, _, _, builder, reviewer, local, _, commands = _harness(
        tmp_path,
        validation_statuses=(CommandStatus.SUCCESS,),
        review_results=(),
        reviewer_failure=(
            AgentStatus.CANCELLED,
            AgentFailure(
                category=AgentFailureCategory.CANCELLATION,
                code="cancelled_during_review",
                message="reviewer cancellation was observed after launch",
                retry=RetryDisposition.RETRYABLE,
                side_effects=SideEffectState.NONE,
            ),
        ),
    )

    result = service.execute(request)

    assert result.status is OrchestrationStatus.CANCELLED
    assert result.run.state is RunState.CANCELLED
    assert builder.invocation_count == reviewer.invocation_count == len(commands.requests) == 1
    assert local.invocation_count == 0
    assert any(record.attempt.status.value == "CANCELLED" for record in result.records)


@pytest.mark.parametrize(
    ("crash_state", "destination"),
    [
        (RunState.BUILDING, RunState.VALIDATING),
        (RunState.VALIDATING, RunState.REVIEWING),
        (RunState.REVIEWING, RunState.APPROVED),
    ],
)
def test_restart_after_durable_outcome_does_not_repeat_side_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    crash_state: RunState,
    destination: RunState,
) -> None:
    service, request, repository, _, builder, reviewer, _, _, commands = _harness(
        tmp_path,
        validation_statuses=(CommandStatus.SUCCESS,),
        review_results=(_approved_review(),),
    )
    persist_transition = repository.persist_transition

    def crash_once(
        expected: StoredRun, result: TransitionResult, *, event_id: EventId
    ) -> StoredRun:
        if expected.run.state is crash_state and result.run.state is destination:
            raise RuntimeError("simulated crash after durable outcome")
        return persist_transition(expected, result, event_id=event_id)

    monkeypatch.setattr(repository, "persist_transition", crash_once)
    with pytest.raises(RuntimeError, match="simulated crash"):
        service.execute(request)
    counts = (builder.invocation_count, reviewer.invocation_count, len(commands.requests))
    monkeypatch.setattr(repository, "persist_transition", persist_transition)
    current = repository.get_run(RUN_ID)
    request_values = request.model_dump(mode="python")
    request_values["expected_revision"] = current.revision

    resumed = service.execute(OrchestrationRequest.model_validate(request_values))

    assert resumed.status is OrchestrationStatus.APPROVED
    assert (builder.invocation_count, reviewer.invocation_count, len(commands.requests)) == (
        1,
        1,
        1,
    )
    assert all(after >= before for before, after in zip(counts, (1, 1, 1), strict=True))


def test_workspace_intent_reconciles_live_owned_worktree_without_recreation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, request, repository, git, builder, reviewer, _, _, commands = _harness(
        tmp_path,
        validation_statuses=(CommandStatus.SUCCESS,),
        review_results=(_approved_review(),),
    )
    create_worktree = git.create_worktree

    def crash_after_create(
        worktree_request: WorktreeCreationRequest,
    ) -> WorktreeCreationResult:
        create_worktree(worktree_request)
        raise RuntimeError("simulated crash after worktree creation")

    monkeypatch.setattr(git, "create_worktree", crash_after_create)
    with pytest.raises(RuntimeError, match="after worktree creation"):
        service.execute(request)
    monkeypatch.setattr(git, "create_worktree", create_worktree)

    reconciled = service.reconcile(request)

    assert reconciled.status is OrchestrationStatus.IN_PROGRESS
    assert reconciled.run.state is RunState.BUILDING
    assert git.create_count == 1
    request_values = request.model_dump(mode="python")
    request_values["expected_revision"] = repository.get_run(RUN_ID).revision
    completed = service.execute(OrchestrationRequest.model_validate(request_values))
    assert completed.status is OrchestrationStatus.APPROVED
    assert git.create_count == builder.invocation_count == reviewer.invocation_count == 1
    assert len(commands.requests) == 1


def test_workspace_reconciliation_rejects_live_ownership_for_another_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, request, _, git, builder, reviewer, local, _, commands = _harness(
        tmp_path,
        validation_statuses=(CommandStatus.SUCCESS,),
        review_results=(_approved_review(),),
    )
    create_worktree = git.create_worktree

    def crash_after_create(
        worktree_request: WorktreeCreationRequest,
    ) -> WorktreeCreationResult:
        create_worktree(worktree_request)
        raise RuntimeError("simulated crash after mismatched worktree creation")

    monkeypatch.setattr(git, "create_worktree", crash_after_create)
    with pytest.raises(RuntimeError, match="mismatched worktree creation"):
        service.execute(request)
    monkeypatch.setattr(git, "create_worktree", create_worktree)
    record_values = git.record.model_dump(mode="python")
    record_values["run_id"] = f"run_{'d' * 32}"
    git.record = WorktreeOwnershipRecord.model_validate(record_values)

    result = service.reconcile(request)

    assert result.status is OrchestrationStatus.BLOCKED
    assert result.run.state is RunState.BLOCKED
    assert git.create_count == 1
    assert builder.invocation_count == reviewer.invocation_count == local.invocation_count == 0
    assert len(commands.requests) == 0
    reconciliation = result.records[-1].reconciliation
    assert reconciliation is not None
    assert reconciliation.state.value == "INCOMPATIBLE"
    assert not reconciliation.safe_to_continue


def test_incomplete_mutating_agent_intent_is_blocked_not_replayed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, request, _, _, builder, reviewer, local, _, commands = _harness(
        tmp_path,
        validation_statuses=(CommandStatus.SUCCESS,),
        review_results=(_approved_review(),),
    )
    invoke = builder.invoke

    def crash_during_invoke(
        agent_request: AgentRequest, *, cancellation: CancellationToken | None = None
    ) -> AgentResponse:
        del agent_request, cancellation
        raise RuntimeError("simulated interrupted mutating invocation")

    monkeypatch.setattr(builder, "invoke", crash_during_invoke)
    with pytest.raises(RuntimeError, match="interrupted mutating"):
        service.execute(request)
    monkeypatch.setattr(builder, "invoke", invoke)

    reconciled = service.reconcile(request)

    assert reconciled.status is OrchestrationStatus.BLOCKED
    assert reconciled.run.state is RunState.BLOCKED
    assert builder.invocation_count == reviewer.invocation_count == local.invocation_count == 0
    assert len(commands.requests) == 0


def test_failed_intent_persistence_rolls_back_and_launches_no_side_effect(
    tmp_path: Path,
) -> None:
    service, request, repository, git, builder, reviewer, local, _, commands = _harness(
        tmp_path,
        validation_statuses=(CommandStatus.SUCCESS,),
        review_results=(_approved_review(),),
    )
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_orchestration_insert
            BEFORE INSERT ON orchestration_records
            BEGIN
                SELECT RAISE(ABORT, 'injected orchestration insert failure');
            END
            """
        )

    with pytest.raises(StorageOperationError, match="persist orchestration record"):
        service.execute(request)

    stored = repository.get_run(RUN_ID)
    assert stored.run.state is RunState.WORKSPACE_PREPARING
    assert repository.list_orchestration_records(RUN_ID) == ()
    assert git.create_count == builder.invocation_count == reviewer.invocation_count == 0
    assert local.invocation_count == len(commands.requests) == 0


def test_missing_validation_executable_blocks_before_review(tmp_path: Path) -> None:
    service, request, _, _, builder, reviewer, _, _, commands = _harness(
        tmp_path,
        validation_statuses=(CommandStatus.POLICY_REJECTED,),
        review_results=(_approved_review(),),
    )

    result = service.execute(request)

    assert result.status is OrchestrationStatus.BLOCKED
    assert builder.invocation_count == len(commands.requests) == 1
    assert reviewer.invocation_count == 0


def test_invalid_validation_evidence_fails_without_repair_or_review(tmp_path: Path) -> None:
    service, request, _, _, _, reviewer, local, _, _ = _harness(
        tmp_path,
        validation_statuses=(CommandStatus.INTERNAL_ERROR,),
        review_results=(_approved_review(),),
        local_repairs=1,
    )

    result = service.execute(request)

    assert result.status is OrchestrationStatus.FAILED
    assert reviewer.invocation_count == local.invocation_count == 0


def test_scope_violation_cannot_approve_or_expand_repair(tmp_path: Path) -> None:
    service, request, repository, _, _, reviewer, local, _, _ = _harness(
        tmp_path,
        validation_statuses=(CommandStatus.SUCCESS,),
        review_results=(_approved_review(),),
        local_repairs=1,
        scope_justified=False,
    )

    result = service.execute(request)

    assert result.status is OrchestrationStatus.FAILED
    assert result.run.approval_gate is None
    assert reviewer.invocation_count == 1
    assert local.invocation_count == 0
    metadata = {
        item.key: item.value for item in repository.list_events(RUN_ID)[-1].transition.metadata
    }
    assert metadata["repair_reasons"] == "scope_violation"


def test_ambiguous_builder_output_is_validated_but_never_auto_repaired(
    tmp_path: Path,
) -> None:
    failure = AgentFailure(
        category=AgentFailureCategory.MALFORMED_OUTPUT,
        code="malformed_terminal",
        message="provider terminal output was malformed after possible writes",
        retry=RetryDisposition.UNKNOWN,
        side_effects=SideEffectState.POSSIBLE,
    )
    service, request, _, _, builder, reviewer, local, _, commands = _harness(
        tmp_path,
        validation_statuses=(CommandStatus.NONZERO_EXIT,),
        review_results=(_approved_review(),),
        local_repairs=1,
        builder_failure=(AgentStatus.INVALID_OUTPUT, failure),
    )

    result = service.execute(request)

    assert result.status is OrchestrationStatus.FAILED
    assert builder.invocation_count == len(commands.requests) == 1
    assert reviewer.invocation_count == local.invocation_count == 0
    assert any(record.attempt.side_effects.value == "AMBIGUOUS" for record in result.records)


def test_high_risk_repair_requires_explicit_codex_authorization(tmp_path: Path) -> None:
    changes = ReviewResult(
        verdict=ReviewVerdict.CHANGES_REQUIRED,
        summary="High-risk persistence defect requires authorized repair.",
        findings=(
            ReviewFinding(
                severity=FindingSeverity.HIGH,
                summary="Persistence recovery is not fail closed.",
            ),
        ),
    )
    service, request, repository, _, _, reviewer, local, codex, _ = _harness(
        tmp_path,
        validation_statuses=(CommandStatus.SUCCESS,),
        review_results=(changes,),
        codex_repairs=1,
        codex_authorized=False,
    )

    result = service.execute(request)

    assert result.status is OrchestrationStatus.BLOCKED
    assert reviewer.invocation_count == 1
    assert local.invocation_count == 0
    assert codex is not None and codex.invocation_count == 0
    metadata = {
        item.key: item.value for item in repository.list_events(RUN_ID)[-1].transition.metadata
    }
    assert metadata["repair_reasons"] == "codex_repair_not_authorized"


def test_repair_limit_is_exact_durable_and_idempotent(tmp_path: Path) -> None:
    service, request, _, _, _, reviewer, local, _, commands = _harness(
        tmp_path,
        validation_statuses=(CommandStatus.NONZERO_EXIT,),
        review_results=(_approved_review(),),
        run=_run(repairs=0),
    )

    result = service.execute(request)
    replay = service.execute(request)

    assert result.status is OrchestrationStatus.FAILED
    assert result.limit_outcome is LimitOutcome.REPAIR_ATTEMPTS_EXHAUSTED
    assert replay.limit_outcome is LimitOutcome.REPAIR_ATTEMPTS_EXHAUSTED
    assert reviewer.invocation_count == local.invocation_count == 0
    assert len(commands.requests) == 1


def test_review_limit_prevents_second_reviewer_invocation(tmp_path: Path) -> None:
    changes = ReviewResult(
        verdict=ReviewVerdict.CHANGES_REQUIRED,
        summary="One bounded mechanical repair is required.",
    )
    service, request, _, _, _, reviewer, local, _, commands = _harness(
        tmp_path,
        validation_statuses=(CommandStatus.SUCCESS, CommandStatus.SUCCESS),
        review_results=(changes,),
        local_repairs=1,
        run=_run(reviews=1),
    )

    result = service.execute(request)

    assert result.status is OrchestrationStatus.FAILED
    assert result.limit_outcome is LimitOutcome.REVIEW_ATTEMPTS_EXHAUSTED
    assert reviewer.invocation_count == local.invocation_count == 1
    assert len(commands.requests) == 2


def test_persisted_build_limit_prevents_builder_launch(tmp_path: Path) -> None:
    run_values = _run(builds=1).model_dump(mode="python")
    run_values["attempts"] = AttemptCounters(build=1)
    service, request, _, git, builder, reviewer, local, _, commands = _harness(
        tmp_path,
        validation_statuses=(CommandStatus.SUCCESS,),
        review_results=(_approved_review(),),
        run=Run.model_validate(run_values),
    )

    result = service.execute(request)

    assert result.status is OrchestrationStatus.FAILED
    assert result.limit_outcome is LimitOutcome.BUILD_ATTEMPTS_EXHAUSTED
    assert git.create_count == 1
    assert builder.invocation_count == reviewer.invocation_count == local.invocation_count == 0
    assert len(commands.requests) == 0


def test_total_duration_limit_is_checked_before_any_side_effect(tmp_path: Path) -> None:
    service, request, _, git, builder, reviewer, local, _, commands = _harness(
        tmp_path,
        validation_statuses=(CommandStatus.SUCCESS,),
        review_results=(_approved_review(),),
        run=_run(duration_seconds=1),
        clock_start=NOW + timedelta(seconds=1),
    )

    result = service.execute(request)

    assert result.status is OrchestrationStatus.BLOCKED
    assert result.limit_outcome is LimitOutcome.RUN_DURATION_EXHAUSTED
    assert git.create_count == builder.invocation_count == reviewer.invocation_count == 0
    assert local.invocation_count == len(commands.requests) == 0
