"""The single authoritative run-state transition implementation."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType

from revanent.domain.errors import ApprovalGateError, InvalidTransitionError
from revanent.domain.models import (
    ApprovalGate,
    AttemptCounterKind,
    AttemptCounters,
    Run,
    RunState,
    StateTransition,
    TransitionMetadata,
    TransitionResult,
)

_CANCELLED = frozenset({RunState.CANCELLED})
_TRANSITIONS: Mapping[RunState, frozenset[RunState]] = MappingProxyType(
    {
        RunState.CREATED: frozenset({RunState.PLANNING, RunState.BLOCKED}) | _CANCELLED,
        RunState.PLANNING: frozenset({RunState.CONTEXT_PREPARING, RunState.BLOCKED}) | _CANCELLED,
        RunState.CONTEXT_PREPARING: frozenset({RunState.WORKSPACE_PREPARING, RunState.BLOCKED})
        | _CANCELLED,
        RunState.WORKSPACE_PREPARING: frozenset({RunState.BUILDING, RunState.BLOCKED}) | _CANCELLED,
        RunState.BUILDING: frozenset({RunState.VALIDATING, RunState.FAILED, RunState.BLOCKED})
        | _CANCELLED,
        RunState.VALIDATING: frozenset(
            {RunState.REVIEWING, RunState.REPAIRING, RunState.FAILED, RunState.BLOCKED}
        )
        | _CANCELLED,
        RunState.REVIEWING: frozenset(
            {RunState.APPROVED, RunState.REPAIRING, RunState.FAILED, RunState.BLOCKED}
        )
        | _CANCELLED,
        RunState.REPAIRING: frozenset({RunState.VALIDATING, RunState.FAILED, RunState.BLOCKED})
        | _CANCELLED,
        RunState.APPROVED: frozenset(),
        RunState.FAILED: frozenset(),
        RunState.BLOCKED: frozenset(),
        RunState.CANCELLED: frozenset(),
    }
)


def permitted_destinations(source: RunState) -> frozenset[RunState]:
    """Return the immutable canonical destinations for ``source``."""
    return _TRANSITIONS[source]


def transition_run(
    run: Run,
    destination: RunState,
    *,
    occurred_at: datetime,
    reason: str,
    metadata: Mapping[str, str] | None = None,
    approval_gate: ApprovalGate | None = None,
    increment_attempt: AttemptCounterKind | None = None,
) -> TransitionResult:
    """Validate a transition and return an immutable next run plus matching evidence."""
    if destination not in _TRANSITIONS[run.state]:
        raise InvalidTransitionError(run.id, run.state, destination)
    attempt_sources = {
        AttemptCounterKind.BUILD: RunState.BUILDING,
        AttemptCounterKind.REVIEW: RunState.REVIEWING,
        AttemptCounterKind.REPAIR: RunState.REPAIRING,
    }
    if increment_attempt is not None and run.state is not attempt_sources[increment_attempt]:
        raise ValueError("attempt counter kind does not match the source run state")

    transition_metadata = tuple(
        TransitionMetadata(key=key, value=value) for key, value in sorted((metadata or {}).items())
    )
    transition = StateTransition(
        run_id=run.id,
        source=run.state,
        destination=destination,
        occurred_at=occurred_at,
        reason=reason,
        metadata=transition_metadata,
    )
    if occurred_at < run.updated_at:
        raise ValueError("transition timestamp cannot be earlier than the current run timestamp")

    if destination is RunState.APPROVED:
        if approval_gate is None:
            raise ApprovalGateError(())
        if not approval_gate.is_satisfied:
            raise ApprovalGateError(approval_gate.failed_gates)
    elif approval_gate is not None:
        raise ApprovalGateError(("unexpected_approval_evidence",))

    next_run_data = run.model_dump(mode="python")
    attempts = run.attempts
    if increment_attempt is not None:
        attempt_data = attempts.model_dump(mode="python")
        attempt_data[increment_attempt.value] += 1
        attempts = AttemptCounters.model_validate(attempt_data)
    next_run_data.update(
        state=destination,
        updated_at=occurred_at,
        approval_gate=approval_gate if destination is RunState.APPROVED else None,
        attempts=attempts,
    )
    next_run = Run.model_validate(next_run_data)
    return TransitionResult(run=next_run, transition=transition)
