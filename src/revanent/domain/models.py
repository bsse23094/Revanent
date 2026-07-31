"""Strict versioned domain schemas."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from revanent.domain.identifiers import EventId, RunId, TaskId, WorkPackageId

SCHEMA_VERSION: Literal[1] = 1
ShortText = Annotated[str, Field(min_length=1, max_length=256)]
LongText = Annotated[str, Field(min_length=1, max_length=8_192)]
PathPattern = Annotated[str, Field(min_length=1, max_length=512)]


class _DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


def utc_now() -> datetime:
    """Return an aware UTC timestamp through one testable boundary."""
    return datetime.now(UTC)


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("timestamp must be timezone-aware UTC")
    return value


class RunState(StrEnum):
    CREATED = "CREATED"
    PLANNING = "PLANNING"
    CONTEXT_PREPARING = "CONTEXT_PREPARING"
    WORKSPACE_PREPARING = "WORKSPACE_PREPARING"
    BUILDING = "BUILDING"
    VALIDATING = "VALIDATING"
    REVIEWING = "REVIEWING"
    REPAIRING = "REPAIRING"
    APPROVED = "APPROVED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        return self in {
            RunState.APPROVED,
            RunState.FAILED,
            RunState.BLOCKED,
            RunState.CANCELLED,
        }


class WorkPackageStatus(StrEnum):
    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    COMPLETE = "COMPLETE"
    SUPERSEDED = "SUPERSEDED"


class ReviewVerdict(StrEnum):
    APPROVED = "APPROVED"
    CHANGES_REQUIRED = "CHANGES_REQUIRED"
    BLOCKED = "BLOCKED"


class FindingSeverity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RunEventType(StrEnum):
    STATE_TRANSITION = "STATE_TRANSITION"


class AttemptCounterKind(StrEnum):
    """Run-level bounded attempt counter selected by orchestration."""

    BUILD = "build"
    REVIEW = "review"
    REPAIR = "repair"


class TaskSpecification(_DomainModel):
    """A bounded task and its source-edit scope."""

    schema_version: Literal[1] = SCHEMA_VERSION
    id: TaskId
    objective: LongText
    allowed_paths: tuple[PathPattern, ...]
    forbidden_paths: tuple[PathPattern, ...] = ()
    acceptance_criteria: tuple[ShortText, ...]

    @model_validator(mode="after")
    def _validate_scope(self) -> Self:
        if not self.allowed_paths:
            raise ValueError("at least one allowed path is required")
        if not self.acceptance_criteria:
            raise ValueError("at least one acceptance criterion is required")
        if len(set(self.allowed_paths)) != len(self.allowed_paths):
            raise ValueError("allowed paths must be unique")
        if len(set(self.forbidden_paths)) != len(self.forbidden_paths):
            raise ValueError("forbidden paths must be unique")
        overlap = set(self.allowed_paths) & set(self.forbidden_paths)
        if overlap:
            raise ValueError(f"paths cannot be both allowed and forbidden: {sorted(overlap)!r}")
        return self


class WorkPackage(_DomainModel):
    """A versioned delivery boundary selected for a run."""

    schema_version: Literal[1] = SCHEMA_VERSION
    id: WorkPackageId
    title: ShortText
    objective: LongText
    status: WorkPackageStatus = WorkPackageStatus.PLANNED


class BudgetLimits(_DomainModel):
    """Hard run limits independent of provider configuration."""

    schema_version: Literal[1] = SCHEMA_VERSION
    max_duration_seconds: int = Field(ge=1, le=604_800)
    max_build_attempts: int = Field(ge=1, le=100)
    max_review_attempts: int = Field(ge=1, le=100)
    max_repair_attempts: int = Field(ge=0, le=100)
    max_remote_tokens: int | None = Field(default=None, ge=1)
    max_estimated_cost_usd: Decimal | None = Field(default=None, gt=0, max_digits=12)


class AttemptCounters(_DomainModel):
    """Persistable counters checked against :class:`BudgetLimits`."""

    build: int = Field(default=0, ge=0)
    review: int = Field(default=0, ge=0)
    repair: int = Field(default=0, ge=0)


class ReviewFinding(_DomainModel):
    severity: FindingSeverity
    summary: ShortText


class ReviewResult(_DomainModel):
    """Versioned structured reviewer output; prose cannot grant approval."""

    schema_version: Literal[1] = SCHEMA_VERSION
    verdict: ReviewVerdict
    summary: LongText
    findings: tuple[ReviewFinding, ...] = ()

    @model_validator(mode="after")
    def _validate_verdict(self) -> Self:
        severe = {
            finding.severity
            for finding in self.findings
            if finding.severity in {FindingSeverity.HIGH, FindingSeverity.CRITICAL}
        }
        if self.verdict is ReviewVerdict.APPROVED and severe:
            raise ValueError("an approved review cannot contain high or critical findings")
        return self


class ApprovalGate(_DomainModel):
    """Local evidence required before a reviewer verdict may become APPROVED."""

    review: ReviewResult
    required_validation_passed: bool
    review_schema_parsed: bool
    scope_justified: bool
    generated_files_consistent: bool
    evidence_complete: bool
    unexplained_dirty_state: bool

    @property
    def failed_gates(self) -> tuple[str, ...]:
        failed: list[str] = []
        if self.review.verdict is not ReviewVerdict.APPROVED:
            failed.append("review_verdict")
        if not self.required_validation_passed:
            failed.append("required_validation")
        if not self.review_schema_parsed:
            failed.append("review_schema")
        if not self.scope_justified:
            failed.append("scope")
        if not self.generated_files_consistent:
            failed.append("generated_files")
        if not self.evidence_complete:
            failed.append("evidence")
        if self.unexplained_dirty_state:
            failed.append("dirty_state")
        return tuple(failed)

    @property
    def is_satisfied(self) -> bool:
        return not self.failed_gates


class Run(_DomainModel):
    """Immutable current state for one bounded execution."""

    schema_version: Literal[1] = SCHEMA_VERSION
    id: RunId
    task: TaskSpecification
    work_package: WorkPackage
    budgets: BudgetLimits
    state: RunState = RunState.CREATED
    attempts: AttemptCounters = Field(default_factory=AttemptCounters)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    approval_gate: ApprovalGate | None = None

    _timestamps_utc = field_validator("created_at", "updated_at")(_require_utc)

    @model_validator(mode="after")
    def _validate_state(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at")
        if self.state is RunState.APPROVED:
            if self.approval_gate is None or not self.approval_gate.is_satisfied:
                raise ValueError("APPROVED state requires complete passing approval evidence")
        elif self.approval_gate is not None:
            raise ValueError("approval evidence is only retained on an APPROVED run")
        if self.attempts.build > self.budgets.max_build_attempts:
            raise ValueError("build attempts exceed the configured limit")
        if self.attempts.review > self.budgets.max_review_attempts:
            raise ValueError("review attempts exceed the configured limit")
        if self.attempts.repair > self.budgets.max_repair_attempts:
            raise ValueError("repair attempts exceed the configured limit")
        return self

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        """Copy a run without permitting Pydantic's unvalidated update shortcut."""
        if update:
            raise TypeError("run updates must use validated domain operations")
        return super().model_copy(update=update, deep=deep)


class TransitionMetadata(_DomainModel):
    key: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")]
    value: Annotated[str, Field(max_length=1_024)]


class StateTransition(_DomainModel):
    """Immutable evidence that the central state machine accepted a change."""

    schema_version: Literal[1] = SCHEMA_VERSION
    run_id: RunId
    source: RunState
    destination: RunState
    occurred_at: datetime
    reason: Annotated[str, Field(min_length=1, max_length=2_048)]
    metadata: tuple[TransitionMetadata, ...] = ()

    _occurred_at_utc = field_validator("occurred_at")(_require_utc)

    @model_validator(mode="after")
    def _validate_metadata(self) -> Self:
        keys = [item.key for item in self.metadata]
        if len(keys) > 32:
            raise ValueError("transition metadata is limited to 32 items")
        if keys != sorted(keys):
            raise ValueError("transition metadata must be sorted by key")
        if len(keys) != len(set(keys)):
            raise ValueError("transition metadata keys must be unique")
        return self


class RunEvent(_DomainModel):
    """Append-only, deterministically ordered evidence for an accepted transition."""

    schema_version: Literal[1] = SCHEMA_VERSION
    id: EventId
    run_id: RunId
    sequence: int = Field(ge=1)
    event_type: RunEventType = RunEventType.STATE_TRANSITION
    occurred_at: datetime
    transition: StateTransition

    _occurred_at_utc = field_validator("occurred_at")(_require_utc)

    @model_validator(mode="after")
    def _validate_transition(self) -> Self:
        if self.transition.run_id != self.run_id:
            raise ValueError("event run identifier does not match its transition")
        if self.transition.occurred_at != self.occurred_at:
            raise ValueError("event timestamp does not match its transition")
        return self


class TransitionResult(_DomainModel):
    """The validated next state and matching transition evidence."""

    run: Run
    transition: StateTransition

    @model_validator(mode="after")
    def _validate_pair(self) -> Self:
        if self.run.id != self.transition.run_id:
            raise ValueError("transition run identifier does not match resulting run")
        if self.run.state is not self.transition.destination:
            raise ValueError("resulting run state does not match transition destination")
        if self.run.updated_at != self.transition.occurred_at:
            raise ValueError("resulting run timestamp does not match transition timestamp")
        return self
