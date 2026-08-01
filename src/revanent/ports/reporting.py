"""Immutable, provider-neutral contracts for bounded evidence reports."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from revanent.domain import RunId, RunState
from revanent.ports.orchestration import AttemptStatus, OrchestrationStep, ReconciliationState
from revanent.ports.telemetry import ReservationStatus, UsageMetric, UsageProvenance, UsageUnit
from revanent.ports.validation import ValidationCommandClass, ValidationStatus

EVIDENCE_REPORT_SCHEMA_VERSION: Literal[1] = 1
MAX_REPORT_ATTEMPTS = 128
MAX_REPORT_ARTIFACTS = 128
MAX_REPORT_FINDINGS = 128
MAX_REPORT_WARNINGS = 64
MAX_REPORT_BYTES = 1_048_576


class EvidenceReportStatus(StrEnum):
    COMPLETE = "COMPLETE"
    COMPLETE_WITH_WARNINGS = "COMPLETE_WITH_WARNINGS"
    INCOMPLETE = "INCOMPLETE"
    INVALID_EVIDENCE = "INVALID_EVIDENCE"
    BLOCKED = "BLOCKED"
    NOT_FOUND = "NOT_FOUND"
    OUTPUT_CONFLICT = "OUTPUT_CONFLICT"
    INTERNAL_FAILURE = "INTERNAL_FAILURE"


class ReportFormat(StrEnum):
    JSON = "json"
    MARKDOWN = "markdown"


class _ReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, str_strip_whitespace=True)


class ReportFailure(_ReportModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    message: str = Field(min_length=1, max_length=512)


class EvidenceReportRequest(_ReportModel):
    schema_version: Literal[1] = EVIDENCE_REPORT_SCHEMA_VERSION
    run_id: RunId


class EvidenceSection(_ReportModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    complete: bool
    reason_codes: tuple[str, ...] = Field(default=(), max_length=MAX_REPORT_WARNINGS)


class ReportArtifact(_ReportModel):
    reference: str = Field(min_length=3, max_length=512)
    content_type: str = Field(min_length=3, max_length=128)
    observed_bytes: int = Field(ge=0)
    stored_bytes: int = Field(ge=0)
    digest_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    complete: bool
    correlation: str | None = Field(default=None, max_length=128)


class ReportAttempt(_ReportModel):
    attempt_id: str = Field(min_length=1, max_length=128)
    kind: OrchestrationStep
    sequence: int = Field(ge=1, le=100)
    status: AttemptStatus
    side_effects: ReconciliationState
    started_at: datetime
    completed_at: datetime | None = None
    role: str | None = Field(default=None, max_length=32)
    adapter_id: str | None = Field(default=None, max_length=128)
    invocation_id: str | None = Field(default=None, max_length=128)
    artifact_references: tuple[str, ...] = Field(default=(), max_length=MAX_REPORT_ARTIFACTS)

    @field_validator("started_at", "completed_at")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value)
        ):
            raise ValueError("report timestamps must be UTC")
        return value


class ReportValidationCommand(_ReportModel):
    command_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    executable: str = Field(min_length=1, max_length=128)
    classification: ValidationCommandClass
    status: ValidationStatus
    expected_exit_codes: tuple[int, ...]
    exit_code: int | None = Field(default=None, ge=0)
    duration_ms: int = Field(ge=0)
    failure_code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    artifact_references: tuple[str, ...] = Field(default=(), max_length=MAX_REPORT_ARTIFACTS)


class ReportValidation(_ReportModel):
    plan_id: str | None = Field(default=None, max_length=128)
    status: ValidationStatus | None = None
    required_commands: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    timed_out: int = Field(ge=0)
    cancelled: int = Field(ge=0)
    unavailable: int = Field(ge=0)
    evidence_complete: bool
    commands: tuple[ReportValidationCommand, ...] = Field(default=(), max_length=64)


class ReportFinding(_ReportModel):
    finding_id: str = Field(min_length=1, max_length=128)
    severity: str = Field(min_length=1, max_length=32)
    summary: str = Field(min_length=1, max_length=256)


class ReportReview(_ReportModel):
    decision: str | None = Field(default=None, max_length=64)
    attempt_status: AttemptStatus | None = None
    approval_gate_present: bool
    approval_gate_valid: bool
    unresolved_high_or_critical: int = Field(ge=0)
    reviewer_adapter_id: str | None = Field(default=None, max_length=128)
    findings: tuple[ReportFinding, ...] = Field(default=(), max_length=MAX_REPORT_FINDINGS)


class ReportContext(_ReportModel):
    manifest_ids: tuple[str, ...] = Field(default=(), max_length=16)
    retained_bytes: int = Field(ge=0)
    baseline_bytes: int = Field(ge=0)
    included_count: int = Field(ge=0)
    excluded_count: int = Field(ge=0)
    required_evidence_complete: bool
    complete: bool


class ReportWorkspace(_ReportModel):
    worktree_id: str | None = Field(default=None, max_length=128)
    relative_path: str | None = Field(default=None, max_length=512)
    branch: str | None = Field(default=None, max_length=255)
    lifecycle: str | None = Field(default=None, max_length=32)
    ownership_verified: bool


class ReportUsage(_ReportModel):
    metric: UsageMetric
    unit: UsageUnit
    provenance: UsageProvenance
    integer_value: int | None = Field(default=None, ge=0)
    decimal_value: Decimal | None = Field(default=None, ge=Decimal("0"))
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    unavailable_count: int = Field(ge=0)


class ReportReservation(_ReportModel):
    reservation_id: str = Field(min_length=1, max_length=128)
    metric: str = Field(min_length=1, max_length=64)
    status: ReservationStatus
    integer_reserved: int | None = Field(default=None, ge=1)
    decimal_reserved: Decimal | None = Field(default=None, gt=Decimal("0"))
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")


class ReproductionEvidence(_ReportModel):
    configuration_schema_version: int = Field(ge=1)
    configuration_digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    repository_id: str | None = Field(default=None, max_length=128)
    worktree_id: str | None = Field(default=None, max_length=128)
    validation_plan_id: str | None = Field(default=None, max_length=128)
    validation_command_ids: tuple[str, ...] = Field(default=(), max_length=64)
    platform: str = Field(min_length=1, max_length=256)
    python_version: str = Field(min_length=1, max_length=128)
    git_version: str = "NOT_PROBED"
    uv_version: str = "NOT_PROBED"
    provider_capabilities: tuple[str, ...] = Field(default=(), max_length=8)


class VerificationEvidence(_ReportModel):
    validation_status: ValidationStatus | None = None
    review_decision: str | None = Field(default=None, max_length=64)
    approval_gate_present: bool
    approval_permitted: bool
    reason_codes: tuple[str, ...] = Field(default=(), max_length=MAX_REPORT_WARNINGS)


class EvidenceReport(_ReportModel):
    schema_version: Literal[1] = EVIDENCE_REPORT_SCHEMA_VERSION
    report_id: str = Field(pattern=r"^report_[0-9a-f]{64}$")
    status: EvidenceReportStatus
    run_id: RunId | None = None
    work_package_id: str | None = Field(default=None, max_length=128)
    generated_at: datetime
    format_version: Literal[1] = 1
    generator_version: str = Field(min_length=1, max_length=64)
    run_state: RunState | None = None
    revision: int | None = Field(default=None, ge=0)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    repository_id: str | None = Field(default=None, max_length=128)
    task_id: str | None = Field(default=None, max_length=128)
    allowed_scope: tuple[str, ...] = Field(default=(), max_length=128)
    forbidden_scope: tuple[str, ...] = Field(default=(), max_length=128)
    terminal_reason_code: str = Field(default="report", pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    cancellation_requested: bool = False
    cancellation_terminal: bool = False
    evidence_complete: bool = False
    contradictory_evidence: bool = False
    contradiction_codes: tuple[str, ...] = Field(default=(), max_length=MAX_REPORT_WARNINGS)
    sections: tuple[EvidenceSection, ...] = Field(default=(), max_length=16)
    workspace: ReportWorkspace = Field(
        default_factory=lambda: ReportWorkspace(ownership_verified=False)
    )
    context: ReportContext = Field(
        default_factory=lambda: ReportContext(
            retained_bytes=0,
            baseline_bytes=0,
            included_count=0,
            excluded_count=0,
            required_evidence_complete=False,
            complete=False,
        )
    )
    attempts: tuple[ReportAttempt, ...] = Field(default=(), max_length=MAX_REPORT_ATTEMPTS)
    validation: ReportValidation = Field(
        default_factory=lambda: ReportValidation(
            required_commands=0,
            passed=0,
            failed=0,
            timed_out=0,
            cancelled=0,
            unavailable=0,
            evidence_complete=False,
        )
    )
    review: ReportReview = Field(
        default_factory=lambda: ReportReview(
            approval_gate_present=False,
            approval_gate_valid=False,
            unresolved_high_or_critical=0,
        )
    )
    usage: tuple[ReportUsage, ...] = Field(default=(), max_length=128)
    reservations: tuple[ReportReservation, ...] = Field(default=(), max_length=128)
    artifacts: tuple[ReportArtifact, ...] = Field(default=(), max_length=MAX_REPORT_ARTIFACTS)
    reproduction: ReproductionEvidence
    verification: VerificationEvidence
    limitations: tuple[str, ...] = Field(default=(), max_length=MAX_REPORT_WARNINGS)
    failure: ReportFailure | None = None

    @field_validator("generated_at", "created_at", "updated_at")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value)
        ):
            raise ValueError("report timestamps must be UTC")
        return value


class EvidenceReportManifest(_ReportModel):
    """Integrity metadata for one explicitly written report artifact, not a signature."""

    report_id: str = Field(pattern=r"^report_[0-9a-f]{64}$")
    format: ReportFormat
    schema_version: Literal[1] = EVIDENCE_REPORT_SCHEMA_VERSION
    source_revision: int | None = Field(default=None, ge=0)
    generated_at: datetime
    artifact_reference: str = Field(min_length=3, max_length=512)
    content_bytes: int = Field(ge=0, le=MAX_REPORT_BYTES)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_complete: bool

    @field_validator("generated_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("report timestamps must be UTC")
        return value


class ReportArtifactWriteResult(_ReportModel):
    artifact: ReportArtifact
    created: bool


class ReportArtifactWriter(Protocol):
    def write(
        self,
        *,
        root: Path,
        relative_path: str,
        data: bytes,
        content_type: str,
        correlation: str,
    ) -> ReportArtifactWriteResult: ...
