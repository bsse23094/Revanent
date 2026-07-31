from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from threading import Barrier

import pytest

from revanent.domain import (
    BudgetLimits,
    Run,
    RunId,
    TaskId,
    TaskSpecification,
    WorkPackage,
    WorkPackageId,
)
from revanent.ports import (
    BudgetDecisionStatus,
    BudgetLimit,
    BudgetMetric,
    BudgetPolicy,
    BudgetReservation,
    BudgetSettlement,
    ReservationStatus,
    UsageMetric,
    UsageProvenance,
    UsageRecord,
    UsageSource,
    UsageUnit,
    reservation_id,
    usage_record_id,
)
from revanent.ports.storage import StorageOperationError
from revanent.storage import SQLiteRunRepository
from revanent.telemetry import TelemetryService

NOW = datetime(2026, 7, 31, 15, tzinfo=UTC)
RUN_ID = RunId(f"run_{'c' * 32}")


def _run() -> Run:
    return Run(
        id=RUN_ID,
        task=TaskSpecification(
            id=TaskId(f"task_{'d' * 32}"),
            objective="Exercise atomic telemetry concurrency.",
            allowed_paths=("src/**",),
            acceptance_criteria=("Exactly one final-capacity reservation succeeds.",),
        ),
        work_package=WorkPackage(
            id=WorkPackageId("P5-002"),
            title="Telemetry concurrency",
            objective="Prove local SQLite serialization.",
        ),
        budgets=BudgetLimits(
            max_duration_seconds=60,
            max_build_attempts=2,
            max_review_attempts=2,
            max_repair_attempts=1,
        ),
        created_at=NOW,
        updated_at=NOW,
    )


def _initialize(path: Path) -> Run:
    repository = SQLiteRunRepository(path)
    repository.initialize()
    run = _run()
    repository.create_run(run)
    return run


def _policy(metric: BudgetMetric) -> BudgetPolicy:
    if metric is BudgetMetric.ESTIMATED_COST:
        limit = BudgetLimit(
            metric=metric,
            unit=UsageUnit.DECIMAL_CURRENCY,
            decimal_limit=Decimal("0.10"),
            currency="USD",
        )
    else:
        unit = {
            BudgetMetric.REMOTE_TOKENS: UsageUnit.TOKENS,
            BudgetMetric.TOTAL_DURATION: UsageUnit.MILLISECONDS,
        }.get(metric, UsageUnit.ATTEMPTS)
        limit = BudgetLimit(
            metric=metric,
            unit=unit,
            integer_limit=(
                10
                if metric is BudgetMetric.REMOTE_TOKENS
                else 1_000
                if metric is BudgetMetric.TOTAL_DURATION
                else 1
            ),
        )
    return BudgetPolicy(limits=(limit,))


def _reservation(
    run: Run,
    metric: BudgetMetric,
    key: str,
    *,
    integer: int | None = None,
    decimal: Decimal | None = None,
    operation: str = "BUILD",
) -> BudgetReservation:
    return BudgetReservation(
        id=reservation_id(run.id, key, metric),
        run_id=run.id,
        work_package_id=run.work_package.id,
        metric=metric,
        unit=(
            UsageUnit.DECIMAL_CURRENCY
            if metric is BudgetMetric.ESTIMATED_COST
            else UsageUnit.TOKENS
            if metric is BudgetMetric.REMOTE_TOKENS
            else UsageUnit.MILLISECONDS
            if metric is BudgetMetric.TOTAL_DURATION
            else UsageUnit.ATTEMPTS
        ),
        operation=operation,
        idempotency_key=key,
        created_at=NOW,
        integer_reserved=integer,
        decimal_reserved=decimal,
        currency="USD" if decimal is not None else None,
    )


@pytest.mark.parametrize(
    ("metric", "integer", "decimal", "operations"),
    [
        (BudgetMetric.BUILD_ATTEMPTS, 1, None, ("BUILD", "REVIEW")),
        (BudgetMetric.REMOTE_TOKENS, 10, None, ("BUILD", "REVIEW")),
        (BudgetMetric.TOTAL_DURATION, 1_000, None, ("VALIDATION", "REVIEW")),
        (
            BudgetMetric.ESTIMATED_COST,
            None,
            Decimal("0.10"),
            ("REVIEW", "CODEX_REPAIR"),
        ),
    ],
)
def test_two_coordinators_cannot_double_spend_final_capacity(
    tmp_path: Path,
    metric: BudgetMetric,
    integer: int | None,
    decimal: Decimal | None,
    operations: tuple[str, str],
) -> None:
    database = tmp_path / "state.db"
    run = _initialize(database)
    barrier = Barrier(2)

    def reserve(index: int) -> BudgetDecisionStatus:
        repository = SQLiteRunRepository(database)
        candidate = _reservation(
            run,
            metric,
            f"race.{index}",
            integer=integer,
            decimal=decimal,
            operation=operations[index],
        )
        barrier.wait()
        return repository.reserve_if_allowed(candidate, _policy(metric)).status

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = tuple(executor.map(reserve, (0, 1)))

    assert sorted(statuses) == sorted(
        (BudgetDecisionStatus.ALLOW, BudgetDecisionStatus.DENY_LIMIT_EXHAUSTED)
    )
    persisted = SQLiteRunRepository(database).list_reservations(run.id)
    assert len(persisted) == 1
    assert persisted[0].status is ReservationStatus.ACTIVE


def test_identical_concurrent_reservations_are_canonical_and_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    run = _initialize(database)
    candidate = _reservation(run, BudgetMetric.BUILD_ATTEMPTS, "same", integer=1)
    barrier = Barrier(2)

    def reserve() -> BudgetDecisionStatus:
        barrier.wait()
        return (
            SQLiteRunRepository(database)
            .reserve_if_allowed(candidate, _policy(BudgetMetric.BUILD_ATTEMPTS))
            .status
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = tuple(executor.map(lambda _: reserve(), range(2)))

    assert statuses == (BudgetDecisionStatus.ALLOW, BudgetDecisionStatus.ALLOW)
    assert SQLiteRunRepository(database).list_reservations(run.id) == (candidate,)


def test_conflicting_concurrent_reservations_reject_one_identity(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    run = _initialize(database)
    policy = BudgetPolicy(
        limits=(
            BudgetLimit(
                metric=BudgetMetric.REMOTE_TOKENS,
                unit=UsageUnit.TOKENS,
                integer_limit=10,
            ),
        )
    )
    candidates = (
        _reservation(run, BudgetMetric.REMOTE_TOKENS, "conflict", integer=4),
        _reservation(run, BudgetMetric.REMOTE_TOKENS, "conflict", integer=5),
    )
    barrier = Barrier(2)

    def reserve(index: int) -> str:
        barrier.wait()
        try:
            decision = SQLiteRunRepository(database).reserve_if_allowed(candidates[index], policy)
        except StorageOperationError:
            return "CONFLICT"
        return decision.status.value

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(reserve, (0, 1)))

    assert sorted(outcomes) == ["ALLOW", "CONFLICT"]
    assert len(SQLiteRunRepository(database).list_reservations(run.id)) == 1


def test_identical_concurrent_settlement_appends_usage_once(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    run = _initialize(database)
    reservation = _reservation(run, BudgetMetric.REMOTE_TOKENS, "settle", integer=10)
    service = TelemetryService(SQLiteRunRepository(database))
    assert (
        service.reserve(reservation, _policy(BudgetMetric.REMOTE_TOKENS)).status
        is BudgetDecisionStatus.ALLOW
    )
    usage = UsageRecord(
        id=usage_record_id(run.id, "settle:tokens", UsageMetric.TOTAL_TOKENS),
        run_id=run.id,
        work_package_id=run.work_package.id,
        metric=UsageMetric.TOTAL_TOKENS,
        unit=UsageUnit.TOKENS,
        provenance=UsageProvenance.PROVIDER_REPORTED,
        source=UsageSource.AGENT,
        observed_at=NOW,
        correlation_key="settle:tokens",
        integer_value=10,
    )
    settlement = BudgetSettlement(
        reservation_id=reservation.id,
        settled_at=NOW,
        integer_consumed=10,
        status=ReservationStatus.SETTLED,
    )
    barrier = Barrier(2)

    def settle() -> bool:
        barrier.wait()
        return TelemetryService(SQLiteRunRepository(database)).settle(
            reservation, settlement, (usage,)
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(lambda _: settle(), range(2)))

    assert sorted(outcomes) == [False, True]
    reopened = SQLiteRunRepository(database)
    assert reopened.list_usage_records(run.id) == (usage,)
    assert reopened.list_reservations(run.id)[0].status is ReservationStatus.SETTLED


def test_conflicting_concurrent_settlements_accept_exactly_one(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    run = _initialize(database)
    reservation = _reservation(run, BudgetMetric.REMOTE_TOKENS, "settlement-conflict", integer=10)
    service = TelemetryService(SQLiteRunRepository(database))
    assert (
        service.reserve(reservation, _policy(BudgetMetric.REMOTE_TOKENS)).status
        is BudgetDecisionStatus.ALLOW
    )
    barrier = Barrier(2)

    def settle(value: int) -> str:
        usage = UsageRecord(
            id=usage_record_id(run.id, "settlement-conflict", UsageMetric.TOTAL_TOKENS),
            run_id=run.id,
            work_package_id=run.work_package.id,
            metric=UsageMetric.TOTAL_TOKENS,
            unit=UsageUnit.TOKENS,
            provenance=UsageProvenance.PROVIDER_REPORTED,
            source=UsageSource.AGENT,
            observed_at=NOW,
            correlation_key="settlement-conflict",
            integer_value=value,
        )
        settlement = BudgetSettlement(
            reservation_id=reservation.id,
            settled_at=NOW,
            integer_consumed=value,
            status=ReservationStatus.SETTLED,
        )
        barrier.wait()
        try:
            TelemetryService(SQLiteRunRepository(database)).settle(
                reservation, settlement, (usage,)
            )
        except StorageOperationError:
            return "CONFLICT"
        return "ACCEPTED"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(settle, (9, 10)))

    assert sorted(outcomes) == ["ACCEPTED", "CONFLICT"]
    reopened = SQLiteRunRepository(database)
    assert len(reopened.list_usage_records(run.id)) == 1
    assert reopened.list_reservations(run.id)[0].status is ReservationStatus.SETTLED


def test_settlement_insert_failure_rolls_back_usage_and_keeps_reservation_active(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.db"
    run = _initialize(database)
    reservation = _reservation(run, BudgetMetric.REMOTE_TOKENS, "rollback", integer=10)
    service = TelemetryService(SQLiteRunRepository(database))
    assert (
        service.reserve(reservation, _policy(BudgetMetric.REMOTE_TOKENS)).status
        is BudgetDecisionStatus.ALLOW
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            """CREATE TRIGGER fail_settlement BEFORE INSERT ON budget_settlements
            BEGIN SELECT RAISE(ABORT, 'injected settlement failure'); END"""
        )
    usage = UsageRecord(
        id=usage_record_id(run.id, "rollback", UsageMetric.TOTAL_TOKENS),
        run_id=run.id,
        work_package_id=run.work_package.id,
        metric=UsageMetric.TOTAL_TOKENS,
        unit=UsageUnit.TOKENS,
        provenance=UsageProvenance.PROVIDER_REPORTED,
        source=UsageSource.AGENT,
        observed_at=NOW,
        correlation_key="rollback",
        integer_value=10,
    )
    settlement = BudgetSettlement(
        reservation_id=reservation.id,
        settled_at=NOW,
        integer_consumed=10,
        status=ReservationStatus.SETTLED,
    )

    with pytest.raises(StorageOperationError):
        service.settle(reservation, settlement, (usage,))

    reopened = SQLiteRunRepository(database)
    assert reopened.list_usage_records(run.id) == ()
    assert reopened.list_reservations(run.id)[0].status is ReservationStatus.ACTIVE


def test_stale_revision_has_no_reservation_side_effect(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    run = _initialize(database)
    reservation = _reservation(run, BudgetMetric.BUILD_ATTEMPTS, "stale", integer=1)

    decision = SQLiteRunRepository(database).reserve_if_allowed(
        reservation,
        _policy(BudgetMetric.BUILD_ATTEMPTS),
        expected_revision=1,
    )

    assert decision.status is BudgetDecisionStatus.DENY_STALE_STATE
    assert SQLiteRunRepository(database).list_reservations(run.id) == ()
