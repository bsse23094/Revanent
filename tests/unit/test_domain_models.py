from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from revanent.domain import (
    ApprovalGate,
    AttemptCounters,
    BudgetLimits,
    ReviewFinding,
    ReviewResult,
    ReviewVerdict,
    Run,
    RunId,
    RunState,
    TaskId,
    TaskSpecification,
    WorkPackage,
    WorkPackageId,
    WorkPackageStatus,
)
from revanent.domain.models import FindingSeverity

NOW = datetime(2026, 7, 30, 12, tzinfo=UTC)


def _task() -> TaskSpecification:
    return TaskSpecification(
        id=TaskId("task_0123456789abcdef0123456789abcdef"),
        objective="Implement the bounded package.",
        allowed_paths=("src/**", "tests/**"),
        forbidden_paths=(".env",),
        acceptance_criteria=("All required tests pass.",),
    )


def _work_package() -> WorkPackage:
    return WorkPackage(
        id=WorkPackageId("P1-001"),
        title="Domain, Configuration, and State Machine",
        objective="Freeze typed boundaries.",
        status=WorkPackageStatus.IN_PROGRESS,
    )


def _budgets() -> BudgetLimits:
    return BudgetLimits(
        max_duration_seconds=5_400,
        max_build_attempts=3,
        max_review_attempts=3,
        max_repair_attempts=2,
        max_remote_tokens=100_000,
        max_estimated_cost_usd=Decimal("25.00"),
    )


def _passing_gate() -> ApprovalGate:
    return ApprovalGate(
        review=ReviewResult(
            verdict=ReviewVerdict.APPROVED,
            summary="All review gates passed.",
        ),
        required_validation_passed=True,
        review_schema_parsed=True,
        scope_justified=True,
        generated_files_consistent=True,
        evidence_complete=True,
        unexplained_dirty_state=False,
    )


def _run(*, state: RunState = RunState.CREATED) -> Run:
    return Run(
        id=RunId("run_0123456789abcdef0123456789abcdef"),
        task=_task(),
        work_package=_work_package(),
        budgets=_budgets(),
        state=state,
        created_at=NOW,
        updated_at=NOW,
        approval_gate=_passing_gate() if state is RunState.APPROVED else None,
    )


def test_stable_identifiers_create_and_validate() -> None:
    first = RunId.new()
    second = RunId.new()

    assert first != second
    assert str(first).startswith("run_")
    assert len(first.root) == 36
    assert TaskId.new().root.startswith("task_")
    assert WorkPackageId("P12-034").root == "P12-034"


@pytest.mark.parametrize(
    ("identifier", "value"),
    [
        (RunId, "run_NOT_HEX"),
        (TaskId, "task_0123"),
        (WorkPackageId, "p1-001"),
        (WorkPackageId, "P1-1"),
    ],
)
def test_stable_identifiers_reject_noncanonical_values(
    identifier: type[RunId] | type[TaskId] | type[WorkPackageId], value: str
) -> None:
    with pytest.raises(ValidationError):
        identifier(value)


def test_run_json_round_trip_is_exact_and_immutable() -> None:
    run = _run()

    restored = Run.model_validate_json(run.model_dump_json())

    assert restored == run
    assert restored.model_dump_json() == run.model_dump_json()
    with pytest.raises(ValidationError):
        restored.__setattr__("state", RunState.BUILDING)

    with pytest.raises(TypeError, match="validated domain operations"):
        restored.model_copy(update={"state": RunState.BUILDING})


def test_approved_state_cannot_be_deserialized_without_passing_evidence() -> None:
    data = _run().model_dump(mode="python")
    data["state"] = RunState.APPROVED

    with pytest.raises(ValidationError, match="APPROVED state requires"):
        Run.model_validate(data)


def test_run_rejects_non_utc_and_over_budget_state() -> None:
    data = _run().model_dump(mode="python")
    data["updated_at"] = datetime(2026, 7, 30, 12)
    with pytest.raises(ValidationError, match="timezone-aware UTC"):
        Run.model_validate(data)

    data = _run().model_dump(mode="python")
    data["attempts"] = AttemptCounters(build=4)
    with pytest.raises(ValidationError, match="build attempts exceed"):
        Run.model_validate(data)


def test_task_scope_is_bounded_and_disjoint() -> None:
    data = _task().model_dump(mode="python")
    data["allowed_paths"] = ()
    with pytest.raises(ValidationError, match="at least one allowed path"):
        TaskSpecification.model_validate(data)

    data = _task().model_dump(mode="python")
    data["forbidden_paths"] = ("src/**",)
    with pytest.raises(ValidationError, match="both allowed and forbidden"):
        TaskSpecification.model_validate(data)


def test_approved_review_rejects_high_or_critical_findings() -> None:
    with pytest.raises(ValidationError, match="approved review cannot contain"):
        ReviewResult(
            verdict=ReviewVerdict.APPROVED,
            summary="Incorrect approval.",
            findings=(
                ReviewFinding(
                    severity=FindingSeverity.HIGH,
                    summary="A required validation gate failed.",
                ),
            ),
        )


def test_approval_gate_reports_every_failed_local_gate() -> None:
    gate = ApprovalGate(
        review=ReviewResult(
            verdict=ReviewVerdict.CHANGES_REQUIRED,
            summary="Changes remain.",
        ),
        required_validation_passed=False,
        review_schema_parsed=False,
        scope_justified=False,
        generated_files_consistent=False,
        evidence_complete=False,
        unexplained_dirty_state=True,
    )

    assert gate.failed_gates == (
        "review_verdict",
        "required_validation",
        "review_schema",
        "scope",
        "generated_files",
        "evidence",
        "dirty_state",
    )
    assert gate.is_satisfied is False


def test_all_work_package_status_values_round_trip() -> None:
    for status in WorkPackageStatus:
        package = _work_package().model_copy(update={"status": status})
        assert WorkPackage.model_validate_json(package.model_dump_json()).status is status
