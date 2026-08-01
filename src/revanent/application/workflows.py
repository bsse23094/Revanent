"""Typed runtime use cases that keep the CLI outside workflow and storage policy."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from revanent.domain import (
    FindingSeverity,
    Run,
    RunEvent,
    RunId,
    RunState,
    TaskSpecification,
    WorkPackage,
)
from revanent.orchestration import OrchestrationService
from revanent.ports.git import GitError, GitRepository, RepositoryIdentity, WorktreeLifecycleStatus
from revanent.ports.orchestration import (
    AttemptStatus,
    ContextAttempt,
    OrchestrationRecord,
    OrchestrationRecordStage,
    OrchestrationRequest,
    OrchestrationResult,
    OrchestrationStatus,
    OrchestrationStep,
    RepairAttempt,
    ReviewAttempt,
    ValidationAttempt,
    WorkspaceAttempt,
)
from revanent.ports.runtime import RuntimeBinding, RuntimeRepository
from revanent.ports.storage import (
    ConcurrentRunUpdateError,
    CorruptStorageError,
    DuplicateEventError,
    RunNotFoundError,
)
from revanent.ports.telemetry import (
    BudgetReservation,
    ReservationStatus,
    UsageMetric,
    UsageProvenance,
    UsageRecord,
    UsageUnit,
)
from revanent.ports.validation import ValidationStatus
from revanent.telemetry import TelemetryService

RUNTIME_SCHEMA_VERSION: Literal[1] = 1


class RuntimeActionStatus(StrEnum):
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    NOT_FOUND = "NOT_FOUND"
    STALE = "STALE"
    INVALID_EVIDENCE = "INVALID_EVIDENCE"


class RuntimeFailureKind(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"
    MISSING_RUN = "MISSING_RUN"
    REPOSITORY_MISMATCH = "REPOSITORY_MISMATCH"
    CONCURRENCY = "CONCURRENCY"
    EVIDENCE = "EVIDENCE"
    INTERNAL = "INTERNAL"


class _RuntimeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, str_strip_whitespace=True)


class RuntimeFailure(_RuntimeModel):
    kind: RuntimeFailureKind
    code: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    message: str = Field(min_length=1, max_length=512)


class StartRunRequest(_RuntimeModel):
    schema_version: Literal[1] = RUNTIME_SCHEMA_VERSION
    task: TaskSpecification
    work_package: WorkPackage


class ResumeRunRequest(_RuntimeModel):
    schema_version: Literal[1] = RUNTIME_SCHEMA_VERSION
    run_id: RunId
    expected_revision: int | None = Field(default=None, ge=0)


class RunStatusRequest(_RuntimeModel):
    schema_version: Literal[1] = RUNTIME_SCHEMA_VERSION
    run_id: RunId


class CancelRunRequest(_RuntimeModel):
    schema_version: Literal[1] = RUNTIME_SCHEMA_VERSION
    run_id: RunId
    expected_revision: int | None = Field(default=None, ge=0)


class _Result(_RuntimeModel):
    schema_version: Literal[1] = RUNTIME_SCHEMA_VERSION
    action_status: RuntimeActionStatus
    run_id: RunId | None = None
    state: RunState | None = None
    revision: int | None = Field(default=None, ge=0)
    work_started: bool = False
    reason: str = Field(min_length=1, max_length=512)
    next_action: str = Field(min_length=1, max_length=256)
    failure: RuntimeFailure | None = None


class StartRunResult(_Result):
    pass


class ResumeRunResult(_Result):
    pass


class CancelRunResult(_Result):
    cancelled: bool = False


class AttemptStatusSummary(_RuntimeModel):
    build: AttemptStatus | None = None
    validation: AttemptStatus | None = None
    review: AttemptStatus | None = None
    repair: AttemptStatus | None = None
    local_repair: AttemptStatus | None = None
    codex_repair: AttemptStatus | None = None


class EventStatusSummary(_RuntimeModel):
    event_id: str
    source: RunState
    destination: RunState
    occurred_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def _event_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("event timestamp must be UTC")
        return value


class RunAttemptSummary(_RuntimeModel):
    builder: int = Field(ge=0)
    reviewer: int = Field(ge=0)
    local_repair: int = Field(ge=0)
    codex_repair: int = Field(ge=0)
    validation: int = Field(ge=0)
    ambiguous_side_effects: bool = False


class ContextStatusSummary(_RuntimeModel):
    manifest_id: str | None = None
    retained_bytes: int | None = Field(default=None, ge=0)
    baseline_bytes: int | None = Field(default=None, ge=0)
    required_evidence_complete: bool | None = None
    status: str | None = None


class ValidationStatusSummary(_RuntimeModel):
    plan_id: str | None = None
    status: ValidationStatus | None = None
    required_commands: int = Field(default=0, ge=0)
    passed: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    timed_out: int = Field(default=0, ge=0)
    cancelled: int = Field(default=0, ge=0)
    unavailable: int = Field(default=0, ge=0)
    evidence_complete: bool = False


class ReviewStatusSummary(_RuntimeModel):
    decision: str | None = None
    low_findings: int = Field(default=0, ge=0)
    medium_findings: int = Field(default=0, ge=0)
    high_findings: int = Field(default=0, ge=0)
    critical_findings: int = Field(default=0, ge=0)
    unresolved_high_or_critical: int = Field(default=0, ge=0)
    attempt_status: AttemptStatus | None = None
    approval_gate_present: bool = False


class UsageStatusItem(_RuntimeModel):
    metric: UsageMetric
    unit: UsageUnit
    provenance: UsageProvenance
    integer_value: int | None = Field(default=None, ge=0)
    decimal_value: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    unavailable_count: int = Field(default=0, ge=0)


class BudgetStatusSummary(_RuntimeModel):
    duration_remaining_ms: int = Field(ge=0)
    build_attempts_remaining: int = Field(ge=0)
    review_attempts_remaining: int = Field(ge=0)
    repair_attempts_remaining: int = Field(ge=0)
    remote_tokens_remaining: int | None = Field(default=None, ge=0)
    estimated_cost_remaining: Decimal | None = Field(default=None, ge=0)


class RunStatusSnapshot(_Result):
    created_at: datetime | None = None
    updated_at: datetime | None = None
    work_package_id: str | None = None
    repository_id: str | None = None
    worktree_reference: str | None = None
    current_stage: str | None = None
    latest_event: EventStatusSummary | None = None
    execution_status: str = "INCOMPLETE"
    reason_code: str = Field(default="status", pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    attempts: RunAttemptSummary = Field(
        default_factory=lambda: RunAttemptSummary(
            builder=0, reviewer=0, local_repair=0, codex_repair=0, validation=0
        )
    )
    latest_attempts: AttemptStatusSummary = Field(default_factory=AttemptStatusSummary)
    context: ContextStatusSummary = Field(default_factory=ContextStatusSummary)
    validation: ValidationStatusSummary = Field(default_factory=ValidationStatusSummary)
    review: ReviewStatusSummary = Field(default_factory=ReviewStatusSummary)
    usage: tuple[UsageStatusItem, ...] = ()
    budgets: BudgetStatusSummary | None = None
    active_reservations: int = Field(default=0, ge=0)
    unresolved_reservations: int = Field(default=0, ge=0)
    cancellation_requested: bool = False
    cancellation_terminal: bool = False
    in_flight_ambiguity: bool = False
    contradiction_codes: tuple[str, ...] = ()
    artifact_references: tuple[str, ...] = ()
    evidence_complete: bool = True

    @field_validator("created_at", "updated_at")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value)
        ):
            raise ValueError("runtime timestamps must be UTC")
        return value


@dataclass(frozen=True, slots=True)
class RuntimeComposition:
    """Explicit application composition; production and tests provide it deliberately."""

    runs: RuntimeRepository
    telemetry: TelemetryService
    orchestration: OrchestrationService
    git: GitRepository
    repository_root: Path
    make_run: Callable[[StartRunRequest], Run]
    make_binding: Callable[[Run, RepositoryIdentity], RuntimeBinding]
    make_request: Callable[[Run, int | None], OrchestrationRequest]


@dataclass(frozen=True, slots=True)
class StatusComposition:
    """The deliberately smaller read-only dependency set for status projection."""

    runs: RuntimeRepository
    telemetry: TelemetryService
    git: GitRepository
    repository_root: Path


class RunApplicationService:
    def __init__(self, composition: RuntimeComposition) -> None:
        self._composition = composition

    def start(self, request: StartRunRequest) -> StartRunResult:
        run = self._composition.make_run(request)
        try:
            identity = self._composition.git.discover(self._composition.repository_root)
            binding = self._composition.make_binding(run, identity)
            # One atomic commit makes both the Run and its immutable repository
            # binding durable before orchestration can launch any external work.
            stored = self._composition.runs.create_bound_run(run, binding)
            reloaded = self._composition.runs.get_run(run.id)
            if reloaded != stored or self._composition.runs.get_runtime_binding(run.id) != binding:
                raise CorruptStorageError("new Run did not reload with its repository binding")
        except Exception as error:
            return _failure_result(StartRunResult, None, error)
        return _orchestration_result(
            StartRunResult,
            self._execute(stored.run, stored.revision),
            work_started=True,
        )

    def _execute(self, run: Run, revision: int | None) -> OrchestrationResult:
        return self._composition.orchestration.execute(
            self._composition.make_request(run, revision)
        )


class ResumeApplicationService:
    def __init__(self, composition: RuntimeComposition) -> None:
        self._composition = composition

    def resume(self, request: ResumeRunRequest) -> ResumeRunResult:
        try:
            stored = self._composition.runs.get_run(request.run_id)
        except Exception as error:
            return _failure_result(ResumeRunResult, request.run_id, error)
        if request.expected_revision is not None and request.expected_revision != stored.revision:
            return _stale_result(ResumeRunResult, stored.run, stored.revision)
        try:
            identity_failure = _validate_runtime_identity(
                self._composition,
                stored.run,
                verify_worktree=_requires_worktree(self._composition, stored.run),
            )
            if identity_failure is not None:
                return _identity_failure_result(
                    ResumeRunResult, stored.run, stored.revision, identity_failure
                )
            orchestration_request = self._composition.make_request(stored.run, stored.revision)
            reconciled = self._composition.orchestration.reconcile(orchestration_request)
            if reconciled.status in {
                OrchestrationStatus.BLOCKED,
                OrchestrationStatus.FAILED,
                OrchestrationStatus.CANCELLED,
            }:
                return _orchestration_result(ResumeRunResult, reconciled, work_started=False)
            continuation = self._composition.make_request(reconciled.run, reconciled.revision)
            result = self._composition.orchestration.execute(continuation)
        except (ConcurrentRunUpdateError, DuplicateEventError):
            current = self._composition.runs.get_run(request.run_id)
            return _stale_result(ResumeRunResult, current.run, current.revision)
        except Exception as error:
            return _failure_result(ResumeRunResult, request.run_id, error)
        return _orchestration_result(
            ResumeRunResult, result, work_started=not result.run.state.is_terminal
        )


class CancellationApplicationService:
    def __init__(self, composition: RuntimeComposition) -> None:
        self._composition = composition

    def cancel(self, request: CancelRunRequest) -> CancelRunResult:
        try:
            stored = self._composition.runs.get_run(request.run_id)
        except Exception as error:
            return _failure_result(CancelRunResult, request.run_id, error)
        try:
            identity_failure = _validate_runtime_identity(
                self._composition,
                stored.run,
                verify_worktree=_requires_worktree(self._composition, stored.run),
            )
            if identity_failure is not None:
                return _identity_failure_result(
                    CancelRunResult, stored.run, stored.revision, identity_failure
                )
            result = self._composition.orchestration.cancel(
                self._composition.make_request(stored.run, request.expected_revision)
            )
        except (ConcurrentRunUpdateError, DuplicateEventError):
            current = self._composition.runs.get_run(request.run_id)
            return _stale_result(CancelRunResult, current.run, current.revision)
        converted = _orchestration_result(CancelRunResult, result, work_started=False)
        return CancelRunResult.model_validate(
            {
                **converted.model_dump(mode="python"),
                "cancelled": result.run.state is RunState.CANCELLED,
            }
        )


class StatusApplicationService:
    def __init__(self, composition: StatusComposition | RuntimeComposition) -> None:
        self._composition = composition

    def status(self, request: RunStatusRequest) -> RunStatusSnapshot:
        try:
            stored = self._composition.runs.get_run(request.run_id)
            binding = self._composition.runs.get_runtime_binding(request.run_id)
            events = self._composition.runs.list_events(request.run_id)
            records = self._composition.runs.list_orchestration_records(request.run_id)
            usage = self._composition.telemetry.usage_records(request.run_id)
            reservations = self._composition.telemetry.reservations(request.run_id)
        except Exception as error:
            return _failure_result(RunStatusSnapshot, request.run_id, error)

        identity_failure = _validate_runtime_identity(
            self._composition,
            stored.run,
            verify_worktree=_requires_worktree(self._composition, stored.run),
        )
        if identity_failure is not None:
            return RunStatusSnapshot(
                action_status=RuntimeActionStatus.BLOCKED,
                run_id=stored.run.id,
                state=stored.run.state,
                revision=stored.revision,
                reason=identity_failure.message,
                next_action="inspect repository and owned-worktree identity",
                failure=identity_failure,
                created_at=stored.run.created_at,
                updated_at=stored.run.updated_at,
                work_package_id=stored.run.work_package.id.root,
                repository_id=binding.repository.repository_id,
                worktree_reference=binding.worktree_relative_path,
                current_stage=stored.run.state.value,
                execution_status="BLOCKED",
                reason_code=identity_failure.code,
                evidence_complete=False,
            )

        latest: dict[OrchestrationStep, AttemptStatus] = {}
        latest_local_repair: AttemptStatus | None = None
        latest_codex_repair: AttemptStatus | None = None
        outcomes: dict[OrchestrationStep, object] = {}
        local_repairs = codex_repairs = validations = 0
        ambiguous = False
        pending = set()
        for record in records:
            if record.stage is OrchestrationRecordStage.INTENT:
                pending.add(record.attempt.attempt_id.root)
            if record.stage is OrchestrationRecordStage.OUTCOME:
                pending.discard(record.attempt.attempt_id.root)
                latest[record.attempt.kind] = record.attempt.status
                outcomes[record.attempt.kind] = record.attempt
                ambiguous = ambiguous or record.attempt.side_effects.value in {
                    "AMBIGUOUS",
                    "INCOMPATIBLE",
                }
                if isinstance(record.attempt, ValidationAttempt):
                    validations += 1
                elif isinstance(record.attempt, RepairAttempt):
                    if record.attempt.role.value == "REPAIRER":
                        codex_repairs += 1
                        latest_codex_repair = record.attempt.status
                    else:
                        local_repairs += 1
                        latest_local_repair = record.attempt.status
            if record.stage is OrchestrationRecordStage.RECONCILIATION:
                pending.discard(record.attempt.attempt_id.root)

        context = _context_summary(outcomes.get(OrchestrationStep.CONTEXT))
        validation = _validation_summary(outcomes.get(OrchestrationStep.VALIDATION))
        review = _review_summary(outcomes.get(OrchestrationStep.REVIEW), stored.run)
        usage_summary = _usage_summary(usage)
        active = sum(item.status is ReservationStatus.ACTIVE for item in reservations)
        unresolved = sum(item.status is ReservationStatus.UNRESOLVED for item in reservations)
        contradiction_codes = _contradictions(
            stored.run,
            stored.revision,
            events,
            records,
            review,
        )
        evidence_complete = not pending and not contradiction_codes and not ambiguous
        status = (
            RuntimeActionStatus.INVALID_EVIDENCE
            if contradiction_codes
            else _status_for_run(stored.run.state)
        )
        reason = (
            "durable evidence is internally contradictory"
            if contradiction_codes
            else "durable status projection"
        )
        return RunStatusSnapshot(
            action_status=status,
            run_id=stored.run.id,
            state=stored.run.state,
            revision=stored.revision,
            work_started=stored.run.state is not RunState.CREATED,
            reason=reason,
            next_action=_next_action(stored.run.state),
            failure=(
                RuntimeFailure(
                    kind=RuntimeFailureKind.EVIDENCE,
                    code="invalid_evidence",
                    message="durable evidence is internally contradictory",
                )
                if contradiction_codes
                else None
            ),
            created_at=stored.run.created_at,
            updated_at=stored.run.updated_at,
            work_package_id=stored.run.work_package.id.root,
            repository_id=binding.repository.repository_id,
            worktree_reference=binding.worktree_relative_path,
            current_stage=stored.run.state.value,
            latest_event=_event_summary(events[-1]) if events else None,
            execution_status=_execution_status(stored.run.state),
            reason_code=(
                "invalid_evidence" if contradiction_codes else _reason_code(stored.run.state)
            ),
            attempts=RunAttemptSummary(
                builder=stored.run.attempts.build,
                reviewer=stored.run.attempts.review,
                local_repair=local_repairs,
                codex_repair=codex_repairs,
                validation=validations,
                ambiguous_side_effects=ambiguous,
            ),
            latest_attempts=AttemptStatusSummary(
                build=latest.get(OrchestrationStep.BUILD),
                validation=latest.get(OrchestrationStep.VALIDATION),
                review=latest.get(OrchestrationStep.REVIEW),
                repair=latest.get(OrchestrationStep.REPAIR),
                local_repair=latest_local_repair,
                codex_repair=latest_codex_repair,
            ),
            context=context,
            validation=validation,
            review=review,
            usage=usage_summary,
            budgets=_budget_summary(stored.run, usage, reservations),
            active_reservations=active,
            unresolved_reservations=unresolved,
            cancellation_requested=stored.run.state is RunState.CANCELLED,
            cancellation_terminal=stored.run.state is RunState.CANCELLED,
            in_flight_ambiguity=ambiguous or bool(pending) or unresolved > 0,
            contradiction_codes=contradiction_codes,
            artifact_references=_artifact_references(records),
            evidence_complete=evidence_complete,
        )


def _validate_runtime_identity(
    composition: StatusComposition | RuntimeComposition,
    run: Run,
    *,
    verify_worktree: bool,
) -> RuntimeFailure | None:
    try:
        binding = composition.runs.get_runtime_binding(run.id)
        current = composition.git.discover(composition.repository_root)
    except (GitError, CorruptStorageError):
        return RuntimeFailure(
            kind=RuntimeFailureKind.REPOSITORY_MISMATCH,
            code="repository_identity_unavailable",
            message="repository identity could not be verified safely",
        )
    if current != binding.repository:
        return RuntimeFailure(
            kind=RuntimeFailureKind.REPOSITORY_MISMATCH,
            code="repository_identity_mismatch",
            message="selected repository does not match the Run repository",
        )
    if not verify_worktree:
        return None
    try:
        verified = composition.git.verify_owned_worktree(binding.worktree_id)
    except GitError:
        return RuntimeFailure(
            kind=RuntimeFailureKind.REPOSITORY_MISMATCH,
            code="worktree_identity_mismatch",
            message="owned worktree identity could not be verified safely",
        )
    expected_target = composition.repository_root / binding.worktree_relative_path
    record = verified.record
    if (
        record.lifecycle_status is not WorktreeLifecycleStatus.ACTIVE
        or record.run_id != run.id.root
        or record.repository != binding.repository
        or record.worktree_id != binding.worktree_id
        or record.worktree_path != expected_target
        or record.branch_name != binding.branch_name
        or verified.worktree.path != expected_target
        or verified.worktree.branch != binding.branch_name
        or verified.repository.identity != binding.repository
    ):
        return RuntimeFailure(
            kind=RuntimeFailureKind.REPOSITORY_MISMATCH,
            code="worktree_identity_mismatch",
            message="owned worktree evidence does not match the Run",
        )
    return None


def _run_requires_worktree(state: RunState) -> bool:
    return state in {
        RunState.BUILDING,
        RunState.VALIDATING,
        RunState.REVIEWING,
        RunState.REPAIRING,
        RunState.APPROVED,
    }


def _requires_worktree(composition: StatusComposition | RuntimeComposition, run: Run) -> bool:
    if _run_requires_worktree(run.state):
        return True
    records = composition.runs.list_orchestration_records(run.id)
    return any(
        item.stage is OrchestrationRecordStage.OUTCOME
        and isinstance(item.attempt, WorkspaceAttempt)
        and item.attempt.status is AttemptStatus.COMPLETED
        for item in records
    )


def _context_summary(value: object | None) -> ContextStatusSummary:
    if not isinstance(value, ContextAttempt) or value.manifest is None:
        return ContextStatusSummary()
    manifest = value.manifest
    return ContextStatusSummary(
        manifest_id=manifest.manifest_id,
        retained_bytes=manifest.retained_bytes,
        baseline_bytes=manifest.baseline_bytes,
        required_evidence_complete=manifest.required_evidence_complete,
        status=manifest.status.value,
    )


def _validation_summary(value: object | None) -> ValidationStatusSummary:
    if not isinstance(value, ValidationAttempt) or value.result is None:
        return ValidationStatusSummary()
    result = value.result
    statuses = tuple(item.status for item in result.commands)
    required = sum(
        1 for command in value.plan.commands if command.classification.value == "REQUIRED"
    )
    return ValidationStatusSummary(
        plan_id=value.plan.id.root,
        status=result.status,
        required_commands=required,
        passed=statuses.count(ValidationStatus.PASSED),
        failed=statuses.count(ValidationStatus.FAILED),
        timed_out=statuses.count(ValidationStatus.TIMED_OUT),
        cancelled=statuses.count(ValidationStatus.CANCELLED),
        unavailable=statuses.count(ValidationStatus.UNAVAILABLE),
        evidence_complete=len(result.commands) == len(value.plan.commands),
    )


def _review_summary(value: object | None, run: Run) -> ReviewStatusSummary:
    if not isinstance(value, ReviewAttempt):
        return ReviewStatusSummary(approval_gate_present=run.approval_gate is not None)
    findings = value.gate_decision.finding_evidence if value.gate_decision is not None else ()
    counts = {severity: 0 for severity in FindingSeverity}
    for finding in findings:
        counts[finding.severity] += 1
    high = counts[FindingSeverity.HIGH] + counts[FindingSeverity.CRITICAL]
    return ReviewStatusSummary(
        decision=(value.gate_decision.status.value if value.gate_decision is not None else None),
        low_findings=counts[FindingSeverity.LOW],
        medium_findings=counts[FindingSeverity.MEDIUM],
        high_findings=counts[FindingSeverity.HIGH],
        critical_findings=counts[FindingSeverity.CRITICAL],
        unresolved_high_or_critical=high,
        attempt_status=value.status,
        approval_gate_present=run.approval_gate is not None,
    )


def _usage_summary(records: tuple[UsageRecord, ...]) -> tuple[UsageStatusItem, ...]:
    grouped: dict[
        tuple[UsageMetric, UsageUnit, UsageProvenance, str | None], list[UsageRecord]
    ] = {}
    for record in records:
        grouped.setdefault(
            (record.metric, record.unit, record.provenance, record.currency), []
        ).append(record)
    values = []
    for (metric, unit, provenance, currency), items in sorted(
        grouped.items(), key=lambda item: tuple(str(value) for value in item[0])
    ):
        values.append(
            UsageStatusItem(
                metric=metric,
                unit=unit,
                provenance=provenance,
                integer_value=(
                    sum(item.integer_value or 0 for item in items)
                    if provenance is not UsageProvenance.UNAVAILABLE
                    and unit is not UsageUnit.DECIMAL_CURRENCY
                    else None
                ),
                decimal_value=(
                    sum((item.decimal_value or Decimal("0") for item in items), Decimal("0"))
                    if unit is UsageUnit.DECIMAL_CURRENCY
                    and provenance is not UsageProvenance.UNAVAILABLE
                    else None
                ),
                currency=currency,
                unavailable_count=(len(items) if provenance is UsageProvenance.UNAVAILABLE else 0),
            )
        )
    return tuple(values)


def _budget_summary(
    run: Run,
    usage: tuple[UsageRecord, ...],
    reservations: tuple[BudgetReservation, ...],
) -> BudgetStatusSummary:
    consumed_duration = sum(
        item.integer_value or 0
        for item in usage
        if item.metric in {UsageMetric.PROVIDER_DURATION, UsageMetric.VALIDATION_DURATION}
        and item.provenance is not UsageProvenance.UNAVAILABLE
    )
    reserved_duration = sum(
        item.integer_reserved or 0
        for item in reservations
        if item.metric.value == "TOTAL_DURATION" and item.status is ReservationStatus.ACTIVE
    )
    tokens = sum(
        item.integer_value or 0
        for item in usage
        if item.metric is UsageMetric.TOTAL_TOKENS
        and item.provenance is not UsageProvenance.UNAVAILABLE
    )
    reserved_tokens = sum(
        item.integer_reserved or 0
        for item in reservations
        if item.metric.value == "REMOTE_TOKENS" and item.status is ReservationStatus.ACTIVE
    )
    cost = sum(
        (item.decimal_value or Decimal("0"))
        for item in usage
        if item.metric is UsageMetric.ESTIMATED_COST
        and item.provenance is not UsageProvenance.UNAVAILABLE
    )
    reserved_cost = sum(
        (item.decimal_reserved or Decimal("0"))
        for item in reservations
        if item.metric.value == "ESTIMATED_COST" and item.status is ReservationStatus.ACTIVE
    )
    return BudgetStatusSummary(
        duration_remaining_ms=max(
            0, run.budgets.max_duration_seconds * 1000 - consumed_duration - reserved_duration
        ),
        build_attempts_remaining=max(0, run.budgets.max_build_attempts - run.attempts.build),
        review_attempts_remaining=max(0, run.budgets.max_review_attempts - run.attempts.review),
        repair_attempts_remaining=max(0, run.budgets.max_repair_attempts - run.attempts.repair),
        remote_tokens_remaining=(
            max(0, run.budgets.max_remote_tokens - tokens - reserved_tokens)
            if run.budgets.max_remote_tokens is not None
            else None
        ),
        estimated_cost_remaining=(
            max(Decimal("0"), run.budgets.max_estimated_cost_usd - cost - reserved_cost)
            if run.budgets.max_estimated_cost_usd is not None
            else None
        ),
    )


def _contradictions(
    run: Run,
    revision: int,
    events: tuple[RunEvent, ...],
    records: tuple[OrchestrationRecord, ...],
    review: ReviewStatusSummary,
) -> tuple[str, ...]:
    codes = []
    if revision != len(events):
        codes.append("event_revision_mismatch")
    if events and events[-1].transition.destination is not run.state:
        codes.append("terminal_event_state_mismatch")
    review_outcomes = [
        item.attempt
        for item in records
        if item.stage is OrchestrationRecordStage.OUTCOME
        and isinstance(item.attempt, ReviewAttempt)
    ]
    latest_review = review_outcomes[-1] if review_outcomes else None
    if run.state is RunState.APPROVED:
        decision = latest_review.gate_decision if latest_review is not None else None
        if (
            run.approval_gate is None
            or not review.approval_gate_present
            or decision is None
            or decision.approval_gate != run.approval_gate
        ):
            codes.append("approval_gate_missing")
    intents = {
        item.attempt.attempt_id.root
        for item in records
        if item.stage is OrchestrationRecordStage.INTENT
    }
    if any(
        item.stage is OrchestrationRecordStage.OUTCOME
        and item.attempt.attempt_id.root not in intents
        for item in records
    ):
        codes.append("outcome_without_intent")
    if any(item.run_revision > revision for item in records):
        codes.append("attempt_revision_impossible")
    if tuple(item.sequence for item in records) != tuple(range(1, len(records) + 1)):
        codes.append("attempt_sequence_impossible")
    completed = {
        item.attempt.attempt_id.root
        for item in records
        if item.stage in {OrchestrationRecordStage.OUTCOME, OrchestrationRecordStage.RECONCILIATION}
    }
    if run.state.is_terminal and any(intent not in completed for intent in intents):
        codes.append("terminal_incomplete_attempt")
    for outcome in review_outcomes:
        decision = outcome.gate_decision
        local = outcome.local_evidence
        if (
            outcome.validation_plan.run_id != run.id
            or outcome.validation_plan.work_package_id != run.work_package.id
            or outcome.validation_result.run_id != run.id
            or outcome.validation_result.work_package_id != run.work_package.id
            or outcome.validation_result.plan_id != outcome.validation_plan.id
            or local is None
            or local.run_id != run.id
            or local.work_package_id != run.work_package.id
            or local.validation_plan_id != outcome.validation_plan.id
            or local.review_invocation_id != outcome.invocation_id
            or local.review_adapter_id != outcome.adapter_id
            or decision is None
            or decision.run_id != run.id
            or decision.work_package_id != run.work_package.id
            or decision.validation_plan_id != outcome.validation_plan.id
            or decision.review_invocation_id != outcome.invocation_id
        ):
            codes.append("review_correlation_mismatch")
    return tuple(sorted(set(codes)))


def _artifact_references(records: tuple[OrchestrationRecord, ...]) -> tuple[str, ...]:
    values: set[str] = set()
    for record in records:
        attempt = record.attempt
        response = getattr(attempt, "response", None)
        if response is not None:
            values.update(
                f"{item.root_id}:{item.relative_path.root}" for item in response.artifacts
            )
        result = getattr(attempt, "result", None)
        if result is not None:
            for command in result.commands:
                for output in (command.stdout, command.stderr):
                    if output.artifact is not None:
                        values.add(
                            f"{output.artifact.root_id}:{output.artifact.relative_path.root}"
                        )
    return tuple(sorted(values))[:128]


def _event_summary(event: RunEvent) -> EventStatusSummary:
    return EventStatusSummary(
        event_id=event.id.root,
        source=event.transition.source,
        destination=event.transition.destination,
        occurred_at=event.occurred_at,
    )


def _execution_status(state: RunState) -> str:
    if state is RunState.CREATED:
        return "INCOMPLETE"
    if state is RunState.APPROVED:
        return "TERMINAL"
    if state is RunState.CANCELLED:
        return "CANCELLED"
    if state is RunState.BLOCKED:
        return "BLOCKED"
    if state is RunState.FAILED:
        return "FAILED"
    return "ACTIVE"


def _reason_code(state: RunState) -> str:
    return {
        RunState.CREATED: "created",
        RunState.APPROVED: "approved",
        RunState.CANCELLED: "cancelled",
        RunState.BLOCKED: "blocked",
        RunState.FAILED: "failed",
    }.get(state, "in_progress")


def _orchestration_result[ResultT: _Result](
    type_: type[ResultT], result: OrchestrationResult, *, work_started: bool
) -> ResultT:
    return type_(
        action_status=(
            RuntimeActionStatus.STALE
            if result.status is OrchestrationStatus.STALE
            else _status_for_run(result.run.state)
        ),
        run_id=result.run.id,
        state=result.run.state,
        revision=result.revision,
        work_started=work_started,
        reason=result.reason,
        next_action=_next_action(result.run.state),
    )


def _identity_failure_result[ResultT: _Result](
    type_: type[ResultT], run: Run, revision: int, failure: RuntimeFailure
) -> ResultT:
    return type_(
        action_status=RuntimeActionStatus.BLOCKED,
        run_id=run.id,
        state=run.state,
        revision=revision,
        reason=failure.message,
        next_action="inspect repository and owned-worktree identity",
        failure=failure,
    )


def _failure_result[ResultT: _Result](
    type_: type[ResultT], run_id: RunId | None, error: Exception
) -> ResultT:
    if isinstance(error, RunNotFoundError):
        kind, status, code = (
            RuntimeFailureKind.MISSING_RUN,
            RuntimeActionStatus.NOT_FOUND,
            "run_not_found",
        )
    elif isinstance(error, CorruptStorageError):
        kind, status, code = (
            RuntimeFailureKind.EVIDENCE,
            RuntimeActionStatus.INVALID_EVIDENCE,
            "invalid_evidence",
        )
    else:
        kind, status, code = (
            RuntimeFailureKind.INTERNAL,
            RuntimeActionStatus.FAILED,
            "runtime_failure",
        )
    return type_(
        action_status=status,
        run_id=run_id,
        reason="runtime operation could not complete",
        next_action="inspect status",
        failure=RuntimeFailure(
            kind=kind, code=code, message="runtime operation could not complete"
        ),
    )


def _stale_result[ResultT: _Result](type_: type[ResultT], run: Run, revision: int) -> ResultT:
    return type_(
        action_status=RuntimeActionStatus.STALE,
        run_id=run.id,
        state=run.state,
        revision=revision,
        reason="run revision changed before this operation",
        next_action="run status and retry",
    )


def _status_for_run(state: RunState) -> RuntimeActionStatus:
    if state is RunState.BLOCKED:
        return RuntimeActionStatus.BLOCKED
    if state is RunState.FAILED:
        return RuntimeActionStatus.FAILED
    return RuntimeActionStatus.COMPLETED


def _next_action(state: RunState) -> str:
    if state is RunState.APPROVED:
        return "inspect the approved worktree"
    if state is RunState.CANCELLED:
        return "inspect preserved evidence before any manual recovery"
    if state is RunState.BLOCKED:
        return "inspect status and resolve the reported blocker"
    if state is RunState.FAILED:
        return "inspect status and durable evidence"
    return "revanent resume RUN_ID --repository PATH"
