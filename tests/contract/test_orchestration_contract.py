from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from revanent.ports import (
    ORCHESTRATION_SCHEMA_VERSION,
    OrchestrationAttemptId,
    ReconciliationResult,
    ReconciliationState,
    RepairDecision,
    RepairReason,
    RepairStrategy,
    canonical_orchestration_bytes,
)

ATTEMPT_ID = OrchestrationAttemptId(f"oattempt_{'a' * 32}")
NOW = datetime(2026, 7, 31, 12, tzinfo=UTC)


def _decision() -> RepairDecision:
    return RepairDecision(
        strategy=RepairStrategy.CODEX_REPAIR,
        reasons=(RepairReason.REPEATED_DEFECT,),
        defect_fingerprints=("validation:vcmd_tests:unexpected_exit_code",),
        repair_sequence=2,
    )


def test_orchestration_schema_and_canonical_serialization_are_frozen() -> None:
    decision = _decision()

    assert ORCHESTRATION_SCHEMA_VERSION == 1
    assert RepairDecision.model_validate_json(decision.model_dump_json()) == decision
    assert canonical_orchestration_bytes(decision) == canonical_orchestration_bytes(decision)
    assert b'"schema_version":1' in canonical_orchestration_bytes(decision)


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": 2},
        {"unknown": "field"},
        {"reasons": ()},
        {"defect_fingerprints": ("z", "a")},
    ],
)
def test_repair_decisions_reject_unknown_versions_fields_and_noncanonical_evidence(
    changes: dict[str, object],
) -> None:
    values = _decision().model_dump(mode="python")
    values.update(changes)

    with pytest.raises(ValidationError):
        RepairDecision.model_validate(values)


def test_reconciliation_rejects_unsafe_ambiguous_continuation() -> None:
    with pytest.raises(ValidationError, match="ambiguous or incompatible"):
        ReconciliationResult(
            attempt_id=ATTEMPT_ID,
            state=ReconciliationState.AMBIGUOUS,
            safe_to_continue=True,
            reason="ambiguous mutating attempt",
            observed_at=NOW,
        )


def test_orchestration_identifiers_and_timestamps_fail_closed() -> None:
    with pytest.raises(ValidationError):
        OrchestrationAttemptId("oattempt_not-hex")
    with pytest.raises(ValidationError, match="timezone-aware UTC"):
        ReconciliationResult(
            attempt_id=ATTEMPT_ID,
            state=ReconciliationState.KNOWN_NONE,
            safe_to_continue=False,
            reason="bounded refusal",
            observed_at=NOW.replace(tzinfo=None),
        )
