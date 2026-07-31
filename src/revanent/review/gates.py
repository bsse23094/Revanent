"""Pure local review-gate computation over typed validation and agent evidence."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from revanent.domain import (
    AgentInvocationId,
    ApprovalGate,
    FindingSeverity,
    ReviewFinding,
    ReviewResult,
    ReviewVerdict,
    RunId,
    WorkPackageId,
)
from revanent.ports.agents import (
    AdapterId,
    AgentArtifactStatus,
    AgentResponse,
    AgentRole,
    AgentStatus,
    ReviewerPayload,
    StructuredParseStatus,
)
from revanent.ports.validation import (
    VALIDATION_SCHEMA_VERSION,
    ValidationPlan,
    ValidationPlanId,
    ValidationPlanResult,
    ValidationStatus,
    canonical_validation_bytes,
)
from revanent.validation.runner import aggregate_validation_results

REVIEW_GATE_SCHEMA_VERSION: Literal[1] = 1


class _GateModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=True,
    )


class ReviewGateStatus(StrEnum):
    APPROVABLE = "APPROVABLE"
    CHANGES_REQUIRED = "CHANGES_REQUIRED"
    BLOCKED = "BLOCKED"
    INVALID_EVIDENCE = "INVALID_EVIDENCE"


class ReviewGateReason(StrEnum):
    VALIDATION_CORRELATION = "validation_correlation"
    VALIDATION_RESULT_INVALID = "validation_result_invalid"
    VALIDATION_NOT_APPROVABLE = "validation_not_approvable"
    VALIDATION_INCOMPLETE = "validation_incomplete"
    REVIEW_CORRELATION = "review_correlation"
    REVIEW_ROLE = "review_role"
    REVIEW_NOT_COMPLETED = "review_not_completed"
    REVIEW_SCHEMA = "review_schema"
    REVIEW_PAYLOAD = "review_payload"
    REVIEW_CHRONOLOGY = "review_chronology"
    REVIEW_ARTIFACT_INCOMPLETE = "review_artifact_incomplete"
    REVIEW_NOT_READ_ONLY = "review_not_read_only"
    REVIEW_AUTHORITY_CORRELATION = "review_authority_correlation"
    REVIEW_FINDING_DUPLICATE = "review_finding_duplicate"
    REVIEW_HIGH_FINDING = "review_high_finding"
    REVIEW_VERDICT = "review_verdict"
    LOCAL_EVIDENCE_CORRELATION = "local_evidence_correlation"
    SCOPE_NOT_JUSTIFIED = "scope_not_justified"
    GENERATED_FILES_INCONSISTENT = "generated_files_inconsistent"
    LOCKFILES_INCONSISTENT = "lockfiles_inconsistent"
    EVIDENCE_INCOMPLETE = "evidence_incomplete"
    REQUIRED_ARTIFACTS_INCOMPLETE = "required_artifacts_incomplete"
    REPOSITORY_DIRTY = "repository_dirty"
    SIDE_EFFECTS_AMBIGUOUS = "side_effects_ambiguous"


class LocalApprovalEvidence(_GateModel):
    """Locally verified facts that no provider payload can establish."""

    schema_version: Literal[1] = REVIEW_GATE_SCHEMA_VERSION
    run_id: RunId
    work_package_id: WorkPackageId
    validation_plan_id: ValidationPlanId
    review_invocation_id: AgentInvocationId
    review_adapter_id: AdapterId
    observed_at: datetime
    scope_justified: bool
    generated_files_consistent: bool
    lockfiles_consistent: bool
    evidence_complete: bool
    required_artifacts_complete: bool
    repository_clean: bool
    review_read_only_verified: bool
    side_effects_reconciled: bool

    @field_validator("observed_at")
    @classmethod
    def _validate_timestamp(cls, value: datetime) -> datetime:
        return _require_utc(value)


class ReviewFindingEvidence(_GateModel):
    finding_id: Annotated[str, Field(pattern=r"^finding-[0-9]{3}$")]
    severity: FindingSeverity
    summary: Annotated[str, Field(min_length=1, max_length=256)]


class ReviewGateInput(_GateModel):
    schema_version: Literal[1] = REVIEW_GATE_SCHEMA_VERSION
    expected_run_id: RunId
    expected_work_package_id: WorkPackageId
    expected_review_invocation_id: AgentInvocationId
    validation_plan: ValidationPlan
    validation_result: ValidationPlanResult
    reviewer_response: AgentResponse
    local_evidence: LocalApprovalEvidence
    evaluated_at: datetime

    @field_validator("evaluated_at")
    @classmethod
    def _validate_timestamp(cls, value: datetime) -> datetime:
        return _require_utc(value)


class ReviewGateDecision(_GateModel):
    schema_version: Literal[1] = REVIEW_GATE_SCHEMA_VERSION
    status: ReviewGateStatus
    summary: Annotated[str, Field(min_length=1, max_length=1_024)]
    reasons: tuple[ReviewGateReason, ...]
    run_id: RunId
    work_package_id: WorkPackageId
    validation_plan_id: ValidationPlanId
    review_invocation_id: AgentInvocationId
    validation_schema_version: Literal[1] = VALIDATION_SCHEMA_VERSION
    review_response_schema_version: Literal[1] = 1
    finding_evidence: tuple[ReviewFindingEvidence, ...] = ()
    relevant_finding_ids: tuple[str, ...] = ()
    evaluated_at: datetime
    approval_gate: ApprovalGate | None = None

    @field_validator("evaluated_at")
    @classmethod
    def _validate_timestamp(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def _validate_decision(self) -> Self:
        if len(self.reasons) != len(set(self.reasons)):
            raise ValueError("review-gate reasons must be unique")
        if tuple(sorted(self.relevant_finding_ids)) != self.relevant_finding_ids or len(
            self.relevant_finding_ids
        ) != len(set(self.relevant_finding_ids)):
            raise ValueError("relevant finding IDs must be sorted and unique")
        known_ids = {finding.finding_id for finding in self.finding_evidence}
        if any(item not in known_ids for item in self.relevant_finding_ids):
            raise ValueError("relevant finding IDs must reference normalized findings")
        if self.status is ReviewGateStatus.APPROVABLE:
            if self.reasons or self.approval_gate is None or not self.approval_gate.is_satisfied:
                raise ValueError("approvable decision requires one complete local ApprovalGate")
        elif self.approval_gate is not None or not self.reasons:
            raise ValueError("refused review-gate decisions require reasons and no ApprovalGate")
        return self


class ReviewGate:
    """Compute local approval evidence without I/O, provider calls, or state mutation."""

    def evaluate(self, gate_input: ReviewGateInput) -> ReviewGateDecision:
        if not isinstance(gate_input, ReviewGateInput):
            raise TypeError("gate_input must be a validated ReviewGateInput")
        reasons: list[ReviewGateReason] = []
        plan = gate_input.validation_plan
        validation = gate_input.validation_result
        response = gate_input.reviewer_response
        local = gate_input.local_evidence

        if (
            plan.run_id != gate_input.expected_run_id
            or plan.work_package_id != gate_input.expected_work_package_id
            or validation.plan_id != plan.id
            or validation.run_id != gate_input.expected_run_id
            or validation.work_package_id != gate_input.expected_work_package_id
        ):
            reasons.append(ReviewGateReason.VALIDATION_CORRELATION)
        try:
            replay = aggregate_validation_results(plan, validation.commands)
            if canonical_validation_bytes(replay) != canonical_validation_bytes(validation):
                reasons.append(ReviewGateReason.VALIDATION_RESULT_INVALID)
        except ValueError:
            reasons.append(ReviewGateReason.VALIDATION_RESULT_INVALID)
        if not validation.approvable:
            reasons.append(ReviewGateReason.VALIDATION_NOT_APPROVABLE)
        if not validation.all_evidence_complete:
            reasons.append(ReviewGateReason.VALIDATION_INCOMPLETE)

        if (
            response.run_id != gate_input.expected_run_id
            or response.work_package_id != gate_input.expected_work_package_id
            or response.invocation_id != gate_input.expected_review_invocation_id
        ):
            reasons.append(ReviewGateReason.REVIEW_CORRELATION)
        if response.role is not AgentRole.REVIEWER:
            reasons.append(ReviewGateReason.REVIEW_ROLE)
        if response.status is not AgentStatus.COMPLETED:
            reasons.append(ReviewGateReason.REVIEW_NOT_COMPLETED)

        review: ReviewResult | None = None
        if response.status is AgentStatus.COMPLETED:
            if response.structured_parse_status is not StructuredParseStatus.PARSED:
                reasons.append(ReviewGateReason.REVIEW_SCHEMA)
            if not isinstance(response.payload, ReviewerPayload):
                reasons.append(ReviewGateReason.REVIEW_PAYLOAD)
            else:
                review = response.payload.review
        if review is not None and review.schema_version != 1:
            reasons.append(ReviewGateReason.REVIEW_SCHEMA)
        if validation.completed_at > response.started_at:
            reasons.append(ReviewGateReason.REVIEW_CHRONOLOGY)
        if response.raw_output_artifact is not None or any(
            artifact.status is AgentArtifactStatus.TRUNCATED for artifact in response.artifacts
        ):
            reasons.append(ReviewGateReason.REVIEW_ARTIFACT_INCOMPLETE)
        if not local.review_read_only_verified:
            reasons.append(ReviewGateReason.REVIEW_NOT_READ_ONLY)
        if local.review_adapter_id != response.identity.adapter_id:
            reasons.append(ReviewGateReason.REVIEW_AUTHORITY_CORRELATION)

        if (
            local.run_id != gate_input.expected_run_id
            or local.work_package_id != gate_input.expected_work_package_id
            or local.validation_plan_id != plan.id
            or local.review_invocation_id != gate_input.expected_review_invocation_id
            or local.observed_at < validation.completed_at
            or local.observed_at < response.completed_at
            or local.observed_at > gate_input.evaluated_at
            or response.completed_at > gate_input.evaluated_at
            or validation.completed_at > gate_input.evaluated_at
        ):
            reasons.append(ReviewGateReason.LOCAL_EVIDENCE_CORRELATION)

        findings, duplicate_findings = _normalize_findings(review)
        if duplicate_findings:
            reasons.append(ReviewGateReason.REVIEW_FINDING_DUPLICATE)
        severe_ids = tuple(
            finding.finding_id
            for finding in findings
            if finding.severity in {FindingSeverity.HIGH, FindingSeverity.CRITICAL}
        )
        if severe_ids:
            reasons.append(ReviewGateReason.REVIEW_HIGH_FINDING)
        if review is not None and review.verdict is not ReviewVerdict.APPROVED:
            reasons.append(ReviewGateReason.REVIEW_VERDICT)

        local_checks = (
            (local.scope_justified, ReviewGateReason.SCOPE_NOT_JUSTIFIED),
            (
                local.generated_files_consistent,
                ReviewGateReason.GENERATED_FILES_INCONSISTENT,
            ),
            (local.lockfiles_consistent, ReviewGateReason.LOCKFILES_INCONSISTENT),
            (local.evidence_complete, ReviewGateReason.EVIDENCE_INCOMPLETE),
            (
                local.required_artifacts_complete,
                ReviewGateReason.REQUIRED_ARTIFACTS_INCOMPLETE,
            ),
            (local.repository_clean, ReviewGateReason.REPOSITORY_DIRTY),
            (local.side_effects_reconciled, ReviewGateReason.SIDE_EFFECTS_AMBIGUOUS),
        )
        reasons.extend(reason for passed, reason in local_checks if not passed)
        canonical_reasons = tuple(dict.fromkeys(reasons))

        if not canonical_reasons and review is not None:
            approval = ApprovalGate(
                review=review,
                required_validation_passed=True,
                review_schema_parsed=True,
                scope_justified=True,
                generated_files_consistent=True,
                evidence_complete=True,
                unexplained_dirty_state=False,
            )
            status = ReviewGateStatus.APPROVABLE
            summary = "All local validation and structured review gates passed"
        else:
            approval = None
            status = _refusal_status(response.status, validation.status, canonical_reasons)
            summary = "Local approval was refused by one or more evidence gates"
        relevant_ids = severe_ids
        if review is not None and review.verdict is not ReviewVerdict.APPROVED:
            relevant_ids = tuple(finding.finding_id for finding in findings)
        return ReviewGateDecision(
            status=status,
            summary=summary,
            reasons=canonical_reasons,
            run_id=gate_input.expected_run_id,
            work_package_id=gate_input.expected_work_package_id,
            validation_plan_id=plan.id,
            review_invocation_id=gate_input.expected_review_invocation_id,
            finding_evidence=findings,
            relevant_finding_ids=tuple(sorted(relevant_ids)),
            evaluated_at=gate_input.evaluated_at,
            approval_gate=approval,
        )


def canonical_review_gate_bytes(decision: ReviewGateDecision) -> bytes:
    return json.dumps(
        decision.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _normalize_findings(
    review: ReviewResult | None,
) -> tuple[tuple[ReviewFindingEvidence, ...], bool]:
    if review is None:
        return (), False
    keys = [(finding.severity.value, finding.summary) for finding in review.findings]
    duplicate = len(keys) != len(set(keys))
    severity_order = {
        FindingSeverity.CRITICAL: 0,
        FindingSeverity.HIGH: 1,
        FindingSeverity.MEDIUM: 2,
        FindingSeverity.LOW: 3,
    }
    ordered: tuple[ReviewFinding, ...] = tuple(
        sorted(review.findings, key=lambda item: (severity_order[item.severity], item.summary))
    )
    return (
        tuple(
            ReviewFindingEvidence(
                finding_id=f"finding-{index:03d}",
                severity=finding.severity,
                summary=finding.summary,
            )
            for index, finding in enumerate(ordered, start=1)
        ),
        duplicate,
    )


def _refusal_status(
    response_status: AgentStatus,
    validation_status: ValidationStatus,
    reasons: tuple[ReviewGateReason, ...],
) -> ReviewGateStatus:
    invalid_reasons = {
        ReviewGateReason.VALIDATION_CORRELATION,
        ReviewGateReason.VALIDATION_RESULT_INVALID,
        ReviewGateReason.REVIEW_CORRELATION,
        ReviewGateReason.REVIEW_ROLE,
        ReviewGateReason.REVIEW_SCHEMA,
        ReviewGateReason.REVIEW_PAYLOAD,
        ReviewGateReason.REVIEW_CHRONOLOGY,
        ReviewGateReason.REVIEW_FINDING_DUPLICATE,
        ReviewGateReason.REVIEW_AUTHORITY_CORRELATION,
        ReviewGateReason.LOCAL_EVIDENCE_CORRELATION,
    }
    if any(reason in invalid_reasons for reason in reasons) or response_status in {
        AgentStatus.FAILED,
        AgentStatus.INVALID_OUTPUT,
    }:
        return ReviewGateStatus.INVALID_EVIDENCE
    if response_status in {
        AgentStatus.BLOCKED,
        AgentStatus.TIMED_OUT,
        AgentStatus.CANCELLED,
        AgentStatus.UNAVAILABLE,
    } or validation_status in {
        ValidationStatus.TIMED_OUT,
        ValidationStatus.CANCELLED,
        ValidationStatus.BLOCKED,
        ValidationStatus.UNAVAILABLE,
    }:
        return ReviewGateStatus.BLOCKED
    return ReviewGateStatus.CHANGES_REQUIRED


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("review-gate timestamp must be timezone-aware UTC")
    return value
