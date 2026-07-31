from __future__ import annotations

import pytest

from revanent.orchestration import RepairPolicy
from revanent.ports import (
    RepairPolicyInput,
    RepairReason,
    RepairStrategy,
)


def _evidence(**changes: object) -> RepairPolicyInput:
    values: dict[str, object] = {
        "defect_fingerprints": ("validation:tests:nonzero_exit",),
        "local_builder_available": True,
        "codex_repair_available": True,
        "codex_repair_authorized": True,
        "repairs_remaining": 2,
    }
    values.update(changes)
    return RepairPolicyInput.model_validate(values)


@pytest.mark.parametrize(
    ("changes", "strategy", "reason"),
    [
        ({}, RepairStrategy.LOCAL_BUILDER, RepairReason.MECHANICAL_FIRST_FAILURE),
        (
            {"repeated_defect_count": 1},
            RepairStrategy.CODEX_REPAIR,
            RepairReason.REPEATED_DEFECT,
        ),
        (
            {"high_risk": True},
            RepairStrategy.CODEX_REPAIR,
            RepairReason.HIGH_RISK_DEFECT,
        ),
        (
            {"malformed_builder_repeated": True},
            RepairStrategy.CODEX_REPAIR,
            RepairReason.REPEATED_DEFECT,
        ),
        (
            {"local_builder_available": False},
            RepairStrategy.CODEX_REPAIR,
            RepairReason.LOCAL_REPAIR_UNAVAILABLE,
        ),
        (
            {"codex_repair_available": False, "high_risk": True},
            RepairStrategy.BLOCKED,
            RepairReason.CODEX_REPAIR_UNAVAILABLE,
        ),
        (
            {"codex_repair_authorized": False, "high_risk": True},
            RepairStrategy.BLOCKED,
            RepairReason.CODEX_REPAIR_NOT_AUTHORIZED,
        ),
        (
            {"repairs_remaining": 0},
            RepairStrategy.NO_REPAIR,
            RepairReason.LIMIT_EXHAUSTED,
        ),
        (
            {"cancelled": True},
            RepairStrategy.NO_REPAIR,
            RepairReason.CANCELLED,
        ),
        (
            {"side_effects_reconciled": False},
            RepairStrategy.NO_REPAIR,
            RepairReason.SIDE_EFFECTS_UNRESOLVED,
        ),
        (
            {"scope_valid": False},
            RepairStrategy.NO_REPAIR,
            RepairReason.SCOPE_VIOLATION,
        ),
        (
            {"evidence_valid": False},
            RepairStrategy.NO_REPAIR,
            RepairReason.INVALID_EVIDENCE,
        ),
        (
            {"external_requirement": True},
            RepairStrategy.BLOCKED,
            RepairReason.EXTERNAL_REQUIREMENT,
        ),
    ],
)
def test_repair_policy_selects_one_bounded_authority(
    changes: dict[str, object], strategy: RepairStrategy, reason: RepairReason
) -> None:
    decision = RepairPolicy().decide(_evidence(**changes), repair_sequence=1)

    assert decision.strategy is strategy
    assert reason in decision.reasons
    assert decision.defect_fingerprints == ("validation:tests:nonzero_exit",)


def test_repair_policy_is_deterministic_for_identical_evidence() -> None:
    evidence = _evidence(repeated_defect_count=1)
    policy = RepairPolicy()

    assert policy.decide(evidence, repair_sequence=2) == policy.decide(evidence, repair_sequence=2)


def test_repair_policy_rejects_unvalidated_evidence() -> None:
    with pytest.raises(TypeError, match="validated RepairPolicyInput"):
        RepairPolicy().decide(object(), repair_sequence=1)  # type: ignore[arg-type]
