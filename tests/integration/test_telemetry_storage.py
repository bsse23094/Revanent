from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

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

NOW = datetime(2026, 7, 31, tzinfo=UTC)


def _run() -> Run:
    return Run(
        id=RunId(f"run_{'a' * 32}"),
        task=TaskSpecification(
            id=TaskId(f"task_{'b' * 32}"),
            objective="telemetry",
            allowed_paths=("src/**",),
            acceptance_criteria=("ok",),
        ),
        work_package=WorkPackage(
            id=WorkPackageId("P5-002"), title="Telemetry", objective="Durable usage"
        ),
        budgets=BudgetLimits(
            max_duration_seconds=90,
            max_build_attempts=3,
            max_review_attempts=3,
            max_repair_attempts=2,
        ),
        created_at=NOW,
        updated_at=NOW,
    )


def _reservation(run: Run, *, value: int = 1) -> BudgetReservation:
    return BudgetReservation(
        id=reservation_id(run.id, "builder.1", BudgetMetric.BUILD_ATTEMPTS),
        run_id=run.id,
        work_package_id=run.work_package.id,
        metric=BudgetMetric.BUILD_ATTEMPTS,
        unit=UsageUnit.ATTEMPTS,
        operation="BUILD",
        idempotency_key="builder.1",
        created_at=NOW,
        integer_reserved=value,
    )


def _policy(limit: int = 1) -> BudgetPolicy:
    return BudgetPolicy(
        limits=(
            BudgetLimit(
                metric=BudgetMetric.BUILD_ATTEMPTS, unit=UsageUnit.ATTEMPTS, integer_limit=limit
            ),
        )
    )


def test_atomic_reservation_boundary_and_settlement_round_trip(tmp_path: Path) -> None:
    repository = SQLiteRunRepository(tmp_path / "state.db")
    repository.initialize()
    run = _run()
    stored = repository.create_run(run)
    service = TelemetryService(repository)
    reservation = _reservation(run)

    assert (
        service.reserve(reservation, _policy(), expected_revision=stored.revision).status
        is BudgetDecisionStatus.ALLOW
    )
    assert (
        service.reserve(reservation, _policy(), expected_revision=stored.revision).status
        is BudgetDecisionStatus.ALLOW
    )
    denied = service.reserve(
        _reservation(run, value=1).model_validate(
            {
                **_reservation(run).model_dump(),
                "idempotency_key": "builder.2",
                "id": reservation_id(run.id, "builder.2", BudgetMetric.BUILD_ATTEMPTS),
            }
        ),
        _policy(),
        expected_revision=stored.revision,
    )
    assert denied.status is BudgetDecisionStatus.DENY_LIMIT_EXHAUSTED

    record = UsageRecord(
        id=usage_record_id(run.id, "builder.1:attempt", UsageMetric.LOCAL_ATTEMPTS),
        run_id=run.id,
        work_package_id=run.work_package.id,
        metric=UsageMetric.LOCAL_ATTEMPTS,
        unit=UsageUnit.ATTEMPTS,
        provenance=UsageProvenance.MEASURED,
        source=UsageSource.ORCHESTRATION,
        observed_at=NOW,
        correlation_key="builder.1:attempt",
        integer_value=1,
    )
    settlement = BudgetSettlement(
        reservation_id=reservation.id,
        settled_at=NOW,
        integer_consumed=1,
        status=ReservationStatus.SETTLED,
    )
    assert service.settle(reservation, settlement, (record,)) is True
    assert service.settle(reservation, settlement, (record,)) is False
    assert repository.list_usage_records(run.id) == (record,)
    assert repository.list_reservations(run.id)[0].status is ReservationStatus.SETTLED


def test_decimal_cost_boundary_is_exact_and_currency_mismatch_denies(tmp_path: Path) -> None:
    repository = SQLiteRunRepository(tmp_path / "state.db")
    repository.initialize()
    run = _run()
    repository.create_run(run)
    policy = BudgetPolicy(
        limits=(
            BudgetLimit(
                metric=BudgetMetric.ESTIMATED_COST,
                unit=UsageUnit.DECIMAL_CURRENCY,
                decimal_limit=Decimal("0.30"),
                currency="USD",
            ),
        )
    )
    service = TelemetryService(repository)
    first = BudgetReservation(
        id=reservation_id(run.id, "cost.1", BudgetMetric.ESTIMATED_COST),
        run_id=run.id,
        work_package_id=run.work_package.id,
        metric=BudgetMetric.ESTIMATED_COST,
        unit=UsageUnit.DECIMAL_CURRENCY,
        operation="BUILD",
        idempotency_key="cost.1",
        created_at=NOW,
        decimal_reserved=Decimal("0.10"),
        currency="USD",
    )
    assert service.reserve(first, policy).status is BudgetDecisionStatus.ALLOW
    wrong = BudgetReservation.model_validate(
        {
            **first.model_dump(mode="python"),
            "id": reservation_id(run.id, "cost.2", BudgetMetric.ESTIMATED_COST),
            "idempotency_key": "cost.2",
            "currency": "EUR",
        }
    )
    assert service.reserve(wrong, policy).status is BudgetDecisionStatus.DENY_INVALID_REQUEST


def test_settlement_usage_conflict_rolls_back(tmp_path: Path) -> None:
    repository = SQLiteRunRepository(tmp_path / "state.db")
    repository.initialize()
    run = _run()
    repository.create_run(run)
    reservation = _reservation(run)
    TelemetryService(repository).reserve(reservation, _policy())
    settlement = BudgetSettlement(
        reservation_id=reservation.id,
        settled_at=NOW,
        integer_consumed=1,
        status=ReservationStatus.SETTLED,
    )
    bad = UsageRecord(
        id=usage_record_id(run.id, "bad", UsageMetric.LOCAL_ATTEMPTS),
        run_id=run.id,
        work_package_id=run.work_package.id,
        metric=UsageMetric.LOCAL_ATTEMPTS,
        unit=UsageUnit.ATTEMPTS,
        provenance=UsageProvenance.MEASURED,
        source=UsageSource.ORCHESTRATION,
        observed_at=NOW,
        correlation_key="bad",
        integer_value=1,
    )
    repository.record_usage(bad)
    conflicting = UsageRecord.model_validate({**bad.model_dump(mode="python"), "integer_value": 2})
    with pytest.raises(StorageOperationError):
        TelemetryService(repository).settle(reservation, settlement, (conflicting,))
    assert repository.list_reservations(run.id)[0].status is ReservationStatus.ACTIVE


def test_duration_overage_is_honest_and_blocks_after_reopen(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    repository = SQLiteRunRepository(database)
    repository.initialize()
    run = _run()
    repository.create_run(run)
    policy = BudgetPolicy(
        limits=(
            BudgetLimit(
                metric=BudgetMetric.TOTAL_DURATION,
                unit=UsageUnit.MILLISECONDS,
                integer_limit=1_000,
            ),
        )
    )
    reservation = BudgetReservation(
        id=reservation_id(run.id, "validation.1", BudgetMetric.TOTAL_DURATION),
        run_id=run.id,
        work_package_id=run.work_package.id,
        metric=BudgetMetric.TOTAL_DURATION,
        unit=UsageUnit.MILLISECONDS,
        operation="VALIDATION",
        idempotency_key="validation.1",
        created_at=NOW,
        integer_reserved=1_000,
    )
    service = TelemetryService(repository)
    assert service.reserve(reservation, policy).status is BudgetDecisionStatus.ALLOW
    usage = UsageRecord(
        id=usage_record_id(run.id, "validation.1:duration", UsageMetric.VALIDATION_DURATION),
        run_id=run.id,
        work_package_id=run.work_package.id,
        metric=UsageMetric.VALIDATION_DURATION,
        unit=UsageUnit.MILLISECONDS,
        provenance=UsageProvenance.MEASURED,
        source=UsageSource.VALIDATION,
        observed_at=NOW,
        correlation_key="validation.1:duration",
        integer_value=1_001,
        reason_code="validation_duration_overage",
    )
    settlement = BudgetSettlement(
        reservation_id=reservation.id,
        settled_at=NOW,
        integer_consumed=1_001,
        status=ReservationStatus.SETTLED,
        reason_code="validation_duration_overage",
    )

    assert service.settle(reservation, settlement, (usage,)) is True

    reopened = TelemetryService(SQLiteRunRepository(database))
    denied = reopened.decision(
        run_id=run.id,
        policy=policy,
        metric=BudgetMetric.TOTAL_DURATION,
        requested_integer=1,
    )
    assert denied.status is BudgetDecisionStatus.DENY_LIMIT_EXHAUSTED
    assert denied.reason_code == "validation_duration_overage"
    assert reopened.usage_records(run.id) == (usage,)


def test_unresolved_reservation_survives_reopen_and_retains_capacity(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    repository = SQLiteRunRepository(database)
    repository.initialize()
    run = _run()
    repository.create_run(run)
    reservation = _reservation(run)
    service = TelemetryService(repository)
    assert service.reserve(reservation, _policy()).status is BudgetDecisionStatus.ALLOW

    assert service.mark_unresolved(
        reservation,
        observed_at=NOW,
        reason_code="trusted_outcome_missing",
    )

    reopened = TelemetryService(SQLiteRunRepository(database))
    assert reopened.reservations(run.id)[0].status is ReservationStatus.UNRESOLVED
    denied = reopened.decision(
        run_id=run.id,
        policy=_policy(),
        metric=BudgetMetric.BUILD_ATTEMPTS,
        requested_integer=1,
    )
    assert denied.status is BudgetDecisionStatus.DENY_UNRESOLVED_RESERVATION
    assert reopened.usage_records(run.id) == ()


def test_persisted_provider_provenance_cannot_be_laundered_to_measured(tmp_path: Path) -> None:
    repository = SQLiteRunRepository(tmp_path / "state.db")
    repository.initialize()
    run = _run()
    repository.create_run(run)
    reported = UsageRecord(
        id=usage_record_id(run.id, "provider.tokens", UsageMetric.TOTAL_TOKENS),
        run_id=run.id,
        work_package_id=run.work_package.id,
        metric=UsageMetric.TOTAL_TOKENS,
        unit=UsageUnit.TOKENS,
        provenance=UsageProvenance.PROVIDER_REPORTED,
        source=UsageSource.AGENT,
        observed_at=NOW,
        correlation_key="provider.tokens",
        integer_value=5,
    )
    assert repository.record_usage(reported)
    relabelled = UsageRecord.model_validate(
        {
            **reported.model_dump(mode="python"),
            "provenance": UsageProvenance.MEASURED,
            "source": UsageSource.ORCHESTRATION,
        }
    )

    with pytest.raises(StorageOperationError):
        repository.record_usage(relabelled)

    assert repository.list_usage_records(run.id) == (reported,)
