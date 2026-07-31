"""Version-1 local review-gate decision contract tests."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from pydantic import ValidationError

from revanent.domain import ReviewResult, ReviewVerdict
from revanent.review import (
    LocalApprovalEvidence,
    ReviewGateDecision,
    ReviewGateReason,
    ReviewGateStatus,
    canonical_review_gate_bytes,
)
from tests.agent_factories import NOW
from tests.validation_factories import (
    PLAN_ID,
    REVIEW_INVOCATION_ID,
    RUN_ID,
    WORK_PACKAGE_ID,
    make_local_approval_evidence,
)


def _decision() -> ReviewGateDecision:
    return ReviewGateDecision(
        status=ReviewGateStatus.CHANGES_REQUIRED,
        summary="Local evidence is incomplete",
        reasons=(ReviewGateReason.EVIDENCE_INCOMPLETE,),
        run_id=RUN_ID,
        work_package_id=WORK_PACKAGE_ID,
        validation_plan_id=PLAN_ID,
        review_invocation_id=REVIEW_INVOCATION_ID,
        evaluated_at=NOW + timedelta(seconds=6),
    )


def test_local_evidence_and_gate_decision_round_trip() -> None:
    local = make_local_approval_evidence()
    decision = _decision()

    assert type(local).model_validate_json(local.model_dump_json()) == local
    assert ReviewGateDecision.model_validate_json(decision.model_dump_json()) == decision


def test_review_gate_canonical_serialization_is_stable() -> None:
    decision = _decision()
    encoded = canonical_review_gate_bytes(decision)

    assert encoded == canonical_review_gate_bytes(ReviewGateDecision.model_validate_json(encoded))
    assert (
        encoded
        == json.dumps(
            json.loads(encoded),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )


@pytest.mark.parametrize("contract", [make_local_approval_evidence(), _decision()])
def test_review_gate_contracts_are_immutable(contract: object) -> None:
    with pytest.raises(ValidationError):
        contract.schema_version = 2  # type: ignore[attr-defined]


@pytest.mark.parametrize("contract", [make_local_approval_evidence(), _decision()])
def test_unknown_fields_and_versions_are_rejected(contract: object) -> None:
    data = contract.model_dump(mode="json")  # type: ignore[attr-defined]
    model_type = (
        LocalApprovalEvidence if isinstance(contract, LocalApprovalEvidence) else ReviewGateDecision
    )
    data["unexpected"] = True
    with pytest.raises(ValidationError):
        model_type.model_validate(data)
    data.pop("unexpected")
    data["schema_version"] = 2
    with pytest.raises(ValidationError):
        model_type.model_validate(data)


def test_provider_cannot_construct_approvable_decision_without_local_gate() -> None:
    data = _decision().model_dump()
    data.update({"status": ReviewGateStatus.APPROVABLE, "reasons": ()})

    with pytest.raises(ValidationError, match="ApprovalGate"):
        ReviewGateDecision.model_validate(data)


def test_provider_cannot_add_safe_to_approve_authority_to_review() -> None:
    review = ReviewResult(verdict=ReviewVerdict.APPROVED, summary="Canonical review")
    data = review.model_dump()
    data["safe_to_approve"] = True

    with pytest.raises(ValidationError):
        ReviewResult.model_validate(data)
