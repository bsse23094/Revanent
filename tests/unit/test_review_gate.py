from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from revanent.domain import (
    AgentInvocationId,
    FindingSeverity,
    ReviewFinding,
    ReviewResult,
    ReviewVerdict,
    RunId,
    WorkPackageId,
)
from revanent.ports import (
    AdapterId,
    AgentArtifactKind,
    AgentArtifactReference,
    AgentArtifactStatus,
    AgentFailure,
    AgentFailureCategory,
    AgentResponse,
    AgentRole,
    AgentStatus,
    CapturedOutput,
    CommandFailure,
    CommandFailureCategory,
    CommandRequest,
    CommandResult,
    CommandStatus,
    RepositoryPath,
    RetryDisposition,
    ReviewerPayload,
    SideEffectState,
    StructuredParseStatus,
    ValidationPlanId,
)
from revanent.review import (
    ReviewGate,
    ReviewGateInput,
    ReviewGateReason,
    ReviewGateStatus,
    canonical_review_gate_bytes,
)
from revanent.validation import ValidationRunner
from tests.agent_factories import NOW, make_response
from tests.validation_factories import (
    REVIEW_INVOCATION_ID,
    RUN_ID,
    WORK_PACKAGE_ID,
    make_local_approval_evidence,
    make_validation_plan,
)


@dataclass
class GateCommandRunner:
    status: CommandStatus = CommandStatus.SUCCESS

    def run(self, request: CommandRequest) -> CommandResult:
        started = NOW + timedelta(seconds=1)
        exit_code: int | None = 0 if self.status is CommandStatus.SUCCESS else 7
        failure = None
        if self.status not in {CommandStatus.SUCCESS, CommandStatus.NONZERO_EXIT}:
            exit_code = None
            category = (
                CommandFailureCategory.EXECUTABLE_UNAVAILABLE
                if self.status is CommandStatus.POLICY_REJECTED
                else CommandFailureCategory.TIMEOUT
                if self.status is CommandStatus.TIMEOUT
                else CommandFailureCategory.CANCELLATION
                if self.status is CommandStatus.CANCELLED
                else CommandFailureCategory.LAUNCH
            )
            failure = CommandFailure(category=category, message="sanitized gate command failure")
        return CommandResult(
            correlation_id=request.correlation_id,
            executable=request.executable,
            resolved_executable=Path(__file__).resolve(),
            status=self.status,
            started_at=started,
            completed_at=started + timedelta(seconds=1),
            duration_seconds=1.0,
            stdout=CapturedOutput(
                text="",
                observed_bytes=0,
                retained_bytes=0,
                truncated=False,
            ),
            stderr=CapturedOutput(
                text="",
                observed_bytes=0,
                retained_bytes=0,
                truncated=False,
            ),
            exit_code=exit_code,
            failure=failure,
        )


def _review_response(
    *,
    verdict: ReviewVerdict = ReviewVerdict.APPROVED,
    findings: tuple[ReviewFinding, ...] = (),
    role: AgentRole = AgentRole.REVIEWER,
) -> AgentResponse:
    base = make_response(role)
    payload = (
        ReviewerPayload(
            review=ReviewResult(
                verdict=verdict,
                summary="Deterministic structured review evidence.",
                findings=findings,
            )
        )
        if role is AgentRole.REVIEWER
        else base.payload
    )
    return AgentResponse.model_validate(
        {
            **base.model_dump(),
            "work_package_id": WORK_PACKAGE_ID,
            "started_at": NOW + timedelta(seconds=3),
            "completed_at": NOW + timedelta(seconds=4),
            "duration_ms": 1_000,
            "payload": payload,
        }
    )


def _failed_review_response(status: AgentStatus) -> AgentResponse:
    base = _review_response()
    if status is AgentStatus.BLOCKED:
        category = AgentFailureCategory.EXTERNAL_BLOCKER
    elif status is AgentStatus.TIMED_OUT:
        category = AgentFailureCategory.TIMEOUT
    elif status is AgentStatus.CANCELLED:
        category = AgentFailureCategory.CANCELLATION
    elif status is AgentStatus.UNAVAILABLE:
        category = AgentFailureCategory.ADAPTER_UNAVAILABLE
    else:
        category = AgentFailureCategory.PROVIDER_FAILURE
    return AgentResponse.model_validate(
        {
            **base.model_dump(),
            "status": status,
            "structured_parse_status": StructuredParseStatus.NOT_PROVIDED,
            "payload": None,
            "failure": AgentFailure(
                category=category,
                code="review_terminal_failure",
                message="Structured review did not complete",
                retry=RetryDisposition.UNKNOWN,
                side_effects=SideEffectState.NONE,
            ),
        }
    )


def _gate_input(
    tmp_path: Path,
    *,
    command_status: CommandStatus = CommandStatus.SUCCESS,
    response: AgentResponse | None = None,
    local_changes: dict[str, object] | None = None,
) -> ReviewGateInput:
    plan = make_validation_plan(tmp_path)
    validation = ValidationRunner(GateCommandRunner(command_status)).execute(plan, started_at=NOW)
    local = make_local_approval_evidence()
    if local_changes:
        local = type(local).model_validate({**local.model_dump(), **local_changes})
    return ReviewGateInput(
        expected_run_id=RUN_ID,
        expected_work_package_id=WORK_PACKAGE_ID,
        expected_review_invocation_id=REVIEW_INVOCATION_ID,
        validation_plan=plan,
        validation_result=validation,
        reviewer_response=response or _review_response(),
        local_evidence=local,
        evaluated_at=NOW + timedelta(seconds=6),
    )


def test_complete_local_evidence_creates_satisfied_approval_gate(tmp_path: Path) -> None:
    decision = ReviewGate().evaluate(_gate_input(tmp_path))

    assert decision.status is ReviewGateStatus.APPROVABLE
    assert decision.reasons == ()
    assert decision.approval_gate is not None
    assert decision.approval_gate.is_satisfied
    assert decision.approval_gate.review.verdict is ReviewVerdict.APPROVED


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("scope_justified", ReviewGateReason.SCOPE_NOT_JUSTIFIED),
        (
            "generated_files_consistent",
            ReviewGateReason.GENERATED_FILES_INCONSISTENT,
        ),
        ("lockfiles_consistent", ReviewGateReason.LOCKFILES_INCONSISTENT),
        ("evidence_complete", ReviewGateReason.EVIDENCE_INCOMPLETE),
        (
            "required_artifacts_complete",
            ReviewGateReason.REQUIRED_ARTIFACTS_INCOMPLETE,
        ),
        ("repository_clean", ReviewGateReason.REPOSITORY_DIRTY),
        ("review_read_only_verified", ReviewGateReason.REVIEW_NOT_READ_ONLY),
        ("side_effects_reconciled", ReviewGateReason.SIDE_EFFECTS_AMBIGUOUS),
    ],
)
def test_each_local_policy_gate_fails_closed(
    field: str, reason: ReviewGateReason, tmp_path: Path
) -> None:
    decision = ReviewGate().evaluate(_gate_input(tmp_path, local_changes={field: False}))

    assert decision.status is ReviewGateStatus.CHANGES_REQUIRED
    assert reason in decision.reasons
    assert decision.approval_gate is None


def test_failed_required_validation_cannot_approve(tmp_path: Path) -> None:
    decision = ReviewGate().evaluate(
        _gate_input(tmp_path, command_status=CommandStatus.NONZERO_EXIT)
    )

    assert decision.status is ReviewGateStatus.CHANGES_REQUIRED
    assert ReviewGateReason.VALIDATION_NOT_APPROVABLE in decision.reasons
    assert decision.approval_gate is None


@pytest.mark.parametrize(
    "command_status",
    [CommandStatus.TIMEOUT, CommandStatus.CANCELLED, CommandStatus.POLICY_REJECTED],
)
def test_interrupted_or_unavailable_validation_cannot_approve(
    command_status: CommandStatus, tmp_path: Path
) -> None:
    decision = ReviewGate().evaluate(_gate_input(tmp_path, command_status=command_status))

    assert decision.status is ReviewGateStatus.BLOCKED
    assert ReviewGateReason.VALIDATION_NOT_APPROVABLE in decision.reasons
    assert decision.approval_gate is None


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (AgentStatus.BLOCKED, ReviewGateStatus.BLOCKED),
        (AgentStatus.TIMED_OUT, ReviewGateStatus.BLOCKED),
        (AgentStatus.CANCELLED, ReviewGateStatus.BLOCKED),
        (AgentStatus.UNAVAILABLE, ReviewGateStatus.BLOCKED),
        (AgentStatus.FAILED, ReviewGateStatus.INVALID_EVIDENCE),
    ],
)
def test_incomplete_reviewer_outcomes_cannot_approve(
    status: AgentStatus, expected: ReviewGateStatus, tmp_path: Path
) -> None:
    decision = ReviewGate().evaluate(
        _gate_input(tmp_path, response=_failed_review_response(status))
    )

    assert decision.status is expected
    assert ReviewGateReason.REVIEW_NOT_COMPLETED in decision.reasons
    assert decision.approval_gate is None


@pytest.mark.parametrize("verdict", [ReviewVerdict.CHANGES_REQUIRED, ReviewVerdict.BLOCKED])
def test_provider_verdict_is_necessary_but_not_local_approval(
    verdict: ReviewVerdict, tmp_path: Path
) -> None:
    decision = ReviewGate().evaluate(
        _gate_input(tmp_path, response=_review_response(verdict=verdict))
    )

    assert ReviewGateReason.REVIEW_VERDICT in decision.reasons
    assert decision.approval_gate is None


@pytest.mark.parametrize("role", [AgentRole.BUILDER, AgentRole.REPAIRER])
def test_nonreviewer_response_cannot_supply_review_evidence(
    role: AgentRole, tmp_path: Path
) -> None:
    decision = ReviewGate().evaluate(_gate_input(tmp_path, response=_review_response(role=role)))

    assert decision.status is ReviewGateStatus.INVALID_EVIDENCE
    assert ReviewGateReason.REVIEW_ROLE in decision.reasons
    assert decision.approval_gate is None


def test_duplicate_findings_fail_as_invalid_evidence(tmp_path: Path) -> None:
    finding = ReviewFinding(severity=FindingSeverity.MEDIUM, summary="Repeated finding")
    response = _review_response(
        verdict=ReviewVerdict.CHANGES_REQUIRED,
        findings=(finding, finding),
    )

    decision = ReviewGate().evaluate(_gate_input(tmp_path, response=response))

    assert decision.status is ReviewGateStatus.INVALID_EVIDENCE
    assert ReviewGateReason.REVIEW_FINDING_DUPLICATE in decision.reasons
    assert decision.approval_gate is None


@pytest.mark.parametrize("severity", [FindingSeverity.HIGH, FindingSeverity.CRITICAL])
def test_unresolved_severe_finding_never_approves(
    severity: FindingSeverity, tmp_path: Path
) -> None:
    response = _review_response(
        verdict=ReviewVerdict.CHANGES_REQUIRED,
        findings=(ReviewFinding(severity=severity, summary="Unresolved severe defect"),),
    )

    decision = ReviewGate().evaluate(_gate_input(tmp_path, response=response))

    assert ReviewGateReason.REVIEW_HIGH_FINDING in decision.reasons
    assert decision.relevant_finding_ids == ("finding-001",)
    assert decision.approval_gate is None


def test_mismatched_review_invocation_fails_closed(tmp_path: Path) -> None:
    decision = ReviewGate().evaluate(
        ReviewGateInput.model_validate(
            {
                **_gate_input(tmp_path).model_dump(),
                "expected_review_invocation_id": AgentInvocationId(f"inv_{'9' * 32}"),
            }
        )
    )

    assert decision.status is ReviewGateStatus.INVALID_EVIDENCE
    assert ReviewGateReason.REVIEW_CORRELATION in decision.reasons
    assert ReviewGateReason.LOCAL_EVIDENCE_CORRELATION in decision.reasons


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("expected_run_id", RunId(f"run_{'8' * 32}")),
        ("expected_work_package_id", WorkPackageId("P4-999")),
    ],
)
def test_expected_run_and_work_package_mismatches_fail_closed(
    field: str,
    value: object,
    tmp_path: Path,
) -> None:
    gate_input = _gate_input(tmp_path)
    decision = ReviewGate().evaluate(
        ReviewGateInput.model_validate({**gate_input.model_dump(), field: value})
    )

    assert decision.status is ReviewGateStatus.INVALID_EVIDENCE
    assert ReviewGateReason.VALIDATION_CORRELATION in decision.reasons
    assert ReviewGateReason.REVIEW_CORRELATION in decision.reasons
    assert ReviewGateReason.LOCAL_EVIDENCE_CORRELATION in decision.reasons
    assert decision.approval_gate is None


def test_mismatched_validation_plan_fails_closed(tmp_path: Path) -> None:
    gate_input = _gate_input(tmp_path)
    plan_data = gate_input.validation_plan.model_dump()
    plan_data["id"] = ValidationPlanId(f"vplan_{'8' * 32}")
    mismatched_plan = type(gate_input.validation_plan).model_validate(plan_data)
    altered = ReviewGateInput.model_validate(
        {**gate_input.model_dump(), "validation_plan": mismatched_plan}
    )

    decision = ReviewGate().evaluate(altered)

    assert decision.status is ReviewGateStatus.INVALID_EVIDENCE
    assert ReviewGateReason.VALIDATION_CORRELATION in decision.reasons
    assert ReviewGateReason.VALIDATION_RESULT_INVALID in decision.reasons


def test_reviewer_adapter_identity_must_match_local_authority_evidence(
    tmp_path: Path,
) -> None:
    response = _review_response()
    response = AgentResponse.model_validate(
        {
            **response.model_dump(),
            "identity": {
                **response.identity.model_dump(),
                "adapter_id": AdapterId("codex.repairer"),
            },
        }
    )

    decision = ReviewGate().evaluate(_gate_input(tmp_path, response=response))

    assert decision.status is ReviewGateStatus.INVALID_EVIDENCE
    assert ReviewGateReason.REVIEW_AUTHORITY_CORRELATION in decision.reasons
    assert decision.approval_gate is None


def test_plain_approval_prose_cannot_replace_structured_review(tmp_path: Path) -> None:
    response = _failed_review_response(AgentStatus.FAILED)
    response = AgentResponse.model_validate(
        {**response.model_dump(), "public_text": "APPROVED - everything passed"}
    )

    decision = ReviewGate().evaluate(_gate_input(tmp_path, response=response))

    assert decision.status is ReviewGateStatus.INVALID_EVIDENCE
    assert decision.approval_gate is None


def test_review_that_predates_validation_fails_chronology(tmp_path: Path) -> None:
    response = _review_response()
    response = AgentResponse.model_validate(
        {
            **response.model_dump(),
            "started_at": NOW + timedelta(seconds=1),
            "completed_at": NOW + timedelta(seconds=2),
        }
    )

    decision = ReviewGate().evaluate(_gate_input(tmp_path, response=response))

    assert decision.status is ReviewGateStatus.INVALID_EVIDENCE
    assert ReviewGateReason.REVIEW_CHRONOLOGY in decision.reasons


def test_local_evidence_must_postdate_review_and_validation(tmp_path: Path) -> None:
    decision = ReviewGate().evaluate(
        _gate_input(
            tmp_path,
            local_changes={"observed_at": NOW + timedelta(seconds=3)},
        )
    )

    assert decision.status is ReviewGateStatus.INVALID_EVIDENCE
    assert ReviewGateReason.LOCAL_EVIDENCE_CORRELATION in decision.reasons
    assert decision.approval_gate is None


def test_truncated_review_artifact_cannot_approve(tmp_path: Path) -> None:
    response = _review_response()
    artifact = AgentArtifactReference(
        root_id="review.fixture",
        relative_path=RepositoryPath("review/output.json"),
        kind=AgentArtifactKind.REVIEW,
        content_type="application/json",
        status=AgentArtifactStatus.TRUNCATED,
        observed_bytes=2,
        stored_bytes=1,
        redacted=True,
    )
    response = AgentResponse.model_validate({**response.model_dump(), "artifacts": (artifact,)})

    decision = ReviewGate().evaluate(_gate_input(tmp_path, response=response))

    assert ReviewGateReason.REVIEW_ARTIFACT_INCOMPLETE in decision.reasons
    assert decision.approval_gate is None


def test_raw_review_output_artifact_cannot_approve(tmp_path: Path) -> None:
    response = _review_response()
    artifact = AgentArtifactReference(
        root_id="review.fixture",
        relative_path=RepositoryPath("review/raw.json"),
        kind=AgentArtifactKind.RAW_OUTPUT,
        content_type="application/json",
        status=AgentArtifactStatus.COMPLETE,
        observed_bytes=1,
        stored_bytes=1,
        redacted=True,
    )
    response = AgentResponse.model_validate(
        {**response.model_dump(), "raw_output_artifact": artifact}
    )

    decision = ReviewGate().evaluate(_gate_input(tmp_path, response=response))

    assert ReviewGateReason.REVIEW_ARTIFACT_INCOMPLETE in decision.reasons
    assert decision.approval_gate is None


def test_gate_decision_is_canonical_and_deterministic(tmp_path: Path) -> None:
    gate_input = _gate_input(tmp_path)
    first = ReviewGate().evaluate(gate_input)
    second = ReviewGate().evaluate(
        ReviewGateInput.model_validate_json(gate_input.model_dump_json())
    )

    assert first == second
    assert canonical_review_gate_bytes(first) == canonical_review_gate_bytes(second)


def test_provider_cannot_embed_approval_gate_in_agent_response() -> None:
    data = _review_response().model_dump(mode="json")
    data["approval_gate"] = {"required_validation_passed": True}

    with pytest.raises(ValidationError):
        AgentResponse.model_validate(data)


def test_gate_has_no_run_state_mutation_surface(tmp_path: Path) -> None:
    gate_input = _gate_input(tmp_path)
    original_response = gate_input.reviewer_response
    original_validation = gate_input.validation_result

    ReviewGate().evaluate(gate_input)

    assert gate_input.reviewer_response == original_response
    assert gate_input.validation_result == original_validation
    assert not hasattr(ReviewGate(), "transition_run")
