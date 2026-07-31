"""Pure deterministic repair selection."""

from __future__ import annotations

from revanent.ports.orchestration import (
    RepairDecision,
    RepairPolicyInput,
    RepairReason,
    RepairStrategy,
)


class RepairPolicy:
    """Select one bounded repair authority from local typed evidence only."""

    def decide(self, evidence: RepairPolicyInput, *, repair_sequence: int) -> RepairDecision:
        if not isinstance(evidence, RepairPolicyInput):
            raise TypeError("repair evidence must be a validated RepairPolicyInput")
        strategy: RepairStrategy
        reasons: tuple[RepairReason, ...]
        if evidence.cancelled:
            strategy = RepairStrategy.NO_REPAIR
            reasons = (RepairReason.CANCELLED,)
        elif not evidence.side_effects_reconciled:
            strategy = RepairStrategy.NO_REPAIR
            reasons = (RepairReason.SIDE_EFFECTS_UNRESOLVED,)
        elif not evidence.scope_valid:
            strategy = RepairStrategy.NO_REPAIR
            reasons = (RepairReason.SCOPE_VIOLATION,)
        elif not evidence.evidence_valid:
            strategy = RepairStrategy.NO_REPAIR
            reasons = (RepairReason.INVALID_EVIDENCE,)
        elif evidence.external_requirement:
            strategy = RepairStrategy.BLOCKED
            reasons = (RepairReason.EXTERNAL_REQUIREMENT,)
        elif evidence.repairs_remaining == 0:
            strategy = RepairStrategy.NO_REPAIR
            reasons = (RepairReason.LIMIT_EXHAUSTED,)
        elif (
            evidence.high_risk
            or evidence.repeated_defect_count > 0
            or evidence.malformed_builder_repeated
        ):
            if evidence.codex_repair_available and evidence.codex_repair_authorized:
                strategy = RepairStrategy.CODEX_REPAIR
                reasons = (
                    RepairReason.HIGH_RISK_DEFECT
                    if evidence.high_risk
                    else RepairReason.REPEATED_DEFECT,
                )
            elif evidence.codex_repair_available:
                strategy = RepairStrategy.BLOCKED
                reasons = (RepairReason.CODEX_REPAIR_NOT_AUTHORIZED,)
            else:
                strategy = RepairStrategy.BLOCKED
                reasons = (RepairReason.CODEX_REPAIR_UNAVAILABLE,)
        elif evidence.local_builder_available:
            strategy = RepairStrategy.LOCAL_BUILDER
            reasons = (RepairReason.MECHANICAL_FIRST_FAILURE,)
        elif evidence.codex_repair_available and evidence.codex_repair_authorized:
            strategy = RepairStrategy.CODEX_REPAIR
            reasons = (RepairReason.LOCAL_REPAIR_UNAVAILABLE,)
        elif evidence.codex_repair_available:
            strategy = RepairStrategy.BLOCKED
            reasons = (RepairReason.CODEX_REPAIR_NOT_AUTHORIZED,)
        else:
            strategy = RepairStrategy.BLOCKED
            reasons = (
                RepairReason.LOCAL_REPAIR_UNAVAILABLE,
                RepairReason.CODEX_REPAIR_UNAVAILABLE,
            )
        return RepairDecision(
            strategy=strategy,
            reasons=reasons,
            defect_fingerprints=evidence.defect_fingerprints,
            repair_sequence=repair_sequence,
        )
