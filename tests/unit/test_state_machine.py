from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from revanent.domain import (
    ApprovalGate,
    ApprovalGateError,
    AttemptCounterKind,
    BudgetLimits,
    InvalidTransitionError,
    ReviewResult,
    ReviewVerdict,
    Run,
    RunId,
    RunState,
    TaskId,
    TaskSpecification,
    WorkPackage,
    WorkPackageId,
    permitted_destinations,
    transition_run,
)

NOW = datetime(2026, 7, 30, 12, tzinfo=UTC)
EXPECTED_TRANSITIONS = {
    RunState.CREATED: {RunState.PLANNING, RunState.BLOCKED, RunState.CANCELLED},
    RunState.PLANNING: {
        RunState.CONTEXT_PREPARING,
        RunState.BLOCKED,
        RunState.CANCELLED,
    },
    RunState.CONTEXT_PREPARING: {
        RunState.WORKSPACE_PREPARING,
        RunState.FAILED,
        RunState.BLOCKED,
        RunState.CANCELLED,
    },
    RunState.WORKSPACE_PREPARING: {
        RunState.BUILDING,
        RunState.BLOCKED,
        RunState.CANCELLED,
    },
    RunState.BUILDING: {
        RunState.VALIDATING,
        RunState.FAILED,
        RunState.BLOCKED,
        RunState.CANCELLED,
    },
    RunState.VALIDATING: {
        RunState.REVIEWING,
        RunState.REPAIRING,
        RunState.FAILED,
        RunState.BLOCKED,
        RunState.CANCELLED,
    },
    RunState.REVIEWING: {
        RunState.APPROVED,
        RunState.REPAIRING,
        RunState.FAILED,
        RunState.BLOCKED,
        RunState.CANCELLED,
    },
    RunState.REPAIRING: {
        RunState.VALIDATING,
        RunState.FAILED,
        RunState.BLOCKED,
        RunState.CANCELLED,
    },
    RunState.APPROVED: set(),
    RunState.FAILED: set(),
    RunState.BLOCKED: set(),
    RunState.CANCELLED: set(),
}
PERMITTED_PAIRS = [
    (source, destination)
    for source, destinations in EXPECTED_TRANSITIONS.items()
    for destination in destinations
]


def _passing_gate() -> ApprovalGate:
    return ApprovalGate(
        review=ReviewResult(
            verdict=ReviewVerdict.APPROVED,
            summary="All local and reviewer gates passed.",
        ),
        required_validation_passed=True,
        review_schema_parsed=True,
        scope_justified=True,
        generated_files_consistent=True,
        evidence_complete=True,
        unexplained_dirty_state=False,
    )


def _run(state: RunState) -> Run:
    return Run(
        id=RunId("run_0123456789abcdef0123456789abcdef"),
        task=TaskSpecification(
            id=TaskId("task_0123456789abcdef0123456789abcdef"),
            objective="Test the state machine.",
            allowed_paths=("src/**",),
            acceptance_criteria=("Every transition is checked.",),
        ),
        work_package=WorkPackage(
            id=WorkPackageId("P1-001"),
            title="State machine tests",
            objective="Prove the canonical transition table.",
        ),
        budgets=BudgetLimits(
            max_duration_seconds=60,
            max_build_attempts=1,
            max_review_attempts=1,
            max_repair_attempts=1,
            max_estimated_cost_usd=Decimal("1.00"),
        ),
        state=state,
        created_at=NOW,
        updated_at=NOW,
        approval_gate=_passing_gate() if state is RunState.APPROVED else None,
    )


def test_authoritative_table_matches_documented_transitions() -> None:
    assert set(EXPECTED_TRANSITIONS) == set(RunState)
    for source, expected in EXPECTED_TRANSITIONS.items():
        assert permitted_destinations(source) == frozenset(expected)


@pytest.mark.parametrize(
    ("kind", "source", "destination"),
    [
        (AttemptCounterKind.BUILD, RunState.BUILDING, RunState.VALIDATING),
        (AttemptCounterKind.REVIEW, RunState.REVIEWING, RunState.REPAIRING),
        (AttemptCounterKind.REPAIR, RunState.REPAIRING, RunState.VALIDATING),
    ],
)
def test_transition_can_atomically_increment_one_bounded_attempt(
    kind: AttemptCounterKind, source: RunState, destination: RunState
) -> None:
    run = _run(source)

    result = transition_run(
        run,
        destination,
        occurred_at=NOW + timedelta(seconds=1),
        reason="Attempt completed at a durable transition boundary.",
        increment_attempt=kind,
    )

    assert getattr(result.run.attempts, kind.value) == 1
    assert sum(result.run.attempts.model_dump().values()) == 1


@pytest.mark.parametrize(("source", "destination"), PERMITTED_PAIRS)
def test_every_permitted_transition(source: RunState, destination: RunState) -> None:
    run = _run(source)
    occurred_at = NOW + timedelta(seconds=1)

    result = transition_run(
        run,
        destination,
        occurred_at=occurred_at,
        reason="Documented transition.",
        metadata={"z_key": "last", "a_key": "first"},
        approval_gate=_passing_gate() if destination is RunState.APPROVED else None,
    )

    assert result.run is not run
    assert run.state is source
    assert result.run.state is destination
    assert result.run.updated_at == occurred_at
    assert result.transition.source is source
    assert result.transition.destination is destination
    assert [item.key for item in result.transition.metadata] == ["a_key", "z_key"]


@pytest.mark.parametrize(
    ("source", "destination"),
    [
        (RunState.CREATED, RunState.BUILDING),
        (RunState.PLANNING, RunState.APPROVED),
        (RunState.BUILDING, RunState.REVIEWING),
        (RunState.VALIDATING, RunState.APPROVED),
        (RunState.REPAIRING, RunState.BUILDING),
    ],
)
def test_representative_forbidden_transition(source: RunState, destination: RunState) -> None:
    with pytest.raises(InvalidTransitionError) as captured:
        transition_run(
            _run(source),
            destination,
            occurred_at=NOW + timedelta(seconds=1),
            reason="Forbidden transition.",
        )

    assert captured.value.source is source
    assert captured.value.destination is destination


@pytest.mark.parametrize(
    "terminal",
    [RunState.APPROVED, RunState.FAILED, RunState.BLOCKED, RunState.CANCELLED],
)
def test_every_terminal_state_rejects_all_destinations(terminal: RunState) -> None:
    assert terminal.is_terminal is True
    assert permitted_destinations(terminal) == frozenset()
    for destination in RunState:
        with pytest.raises(InvalidTransitionError):
            transition_run(
                _run(terminal),
                destination,
                occurred_at=NOW + timedelta(seconds=1),
                reason="Terminal states cannot transition.",
            )


def test_approval_requires_present_and_passing_gate() -> None:
    run = _run(RunState.REVIEWING)
    with pytest.raises(ApprovalGateError, match="approval evidence missing"):
        transition_run(
            run,
            RunState.APPROVED,
            occurred_at=NOW + timedelta(seconds=1),
            reason="Missing evidence.",
        )

    failed_gate = _passing_gate().model_copy(update={"evidence_complete": False})
    with pytest.raises(ApprovalGateError) as captured:
        transition_run(
            run,
            RunState.APPROVED,
            occurred_at=NOW + timedelta(seconds=1),
            reason="Incomplete evidence.",
            approval_gate=failed_gate,
        )
    assert captured.value.failed_gates == ("evidence",)


def test_approval_evidence_is_rejected_for_nonapproval_transition() -> None:
    with pytest.raises(ApprovalGateError, match="unexpected_approval_evidence"):
        transition_run(
            _run(RunState.REVIEWING),
            RunState.REPAIRING,
            occurred_at=NOW + timedelta(seconds=1),
            reason="Repair needed.",
            approval_gate=_passing_gate(),
        )


def test_transition_rejects_time_regression_blank_reason_and_oversized_metadata() -> None:
    run = _run(RunState.CREATED)
    with pytest.raises(ValueError, match="earlier than"):
        transition_run(
            run,
            RunState.PLANNING,
            occurred_at=NOW - timedelta(seconds=1),
            reason="Time regression.",
        )

    with pytest.raises(ValidationError):
        transition_run(
            run,
            RunState.PLANNING,
            occurred_at=NOW,
            reason=" ",
        )

    with pytest.raises(ValidationError, match="timezone-aware UTC"):
        transition_run(
            run,
            RunState.PLANNING,
            occurred_at=datetime(2026, 7, 30, 12),
            reason="Naive timestamp.",
        )

    with pytest.raises(ValidationError, match="limited to 32"):
        transition_run(
            run,
            RunState.PLANNING,
            occurred_at=NOW,
            reason="Too much metadata.",
            metadata={f"key_{index:02}": "value" for index in range(33)},
        )


def test_state_transition_json_round_trip_is_deterministic() -> None:
    transition = transition_run(
        _run(RunState.CREATED),
        RunState.PLANNING,
        occurred_at=NOW + timedelta(seconds=1),
        reason="Start planning.",
    ).transition

    restored = type(transition).model_validate_json(transition.model_dump_json())

    assert restored == transition
    assert restored.model_dump_json() == transition.model_dump_json()
