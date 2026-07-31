"""Pure aggregation and durable preflight helpers for telemetry."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from revanent.context.models import ContextManifest
from revanent.domain import RunId
from revanent.ports.agents import AgentResponse
from revanent.ports.telemetry import (
    BudgetDecision,
    BudgetDecisionStatus,
    BudgetMetric,
    BudgetPolicy,
    BudgetReservation,
    BudgetSettlement,
    ReservationStatus,
    TelemetryRepository,
    UsageMetric,
    UsageProvenance,
    UsageRecord,
    UsageSource,
    UsageUnit,
    usage_record_id,
)


class TelemetryService:
    """Apply exact, fail-closed budget decisions over durable append-only evidence."""

    def __init__(self, repository: TelemetryRepository) -> None:
        self._repository = repository

    def decision(
        self,
        *,
        run_id: RunId,
        policy: BudgetPolicy,
        metric: BudgetMetric,
        requested_integer: int | None = None,
        requested_decimal: Decimal | None = None,
        currency: str | None = None,
        require_known: bool = False,
    ) -> BudgetDecision:
        limit = next((item for item in policy.limits if item.metric is metric), None)
        unit = _unit(metric)
        if limit is None:
            return BudgetDecision(status=BudgetDecisionStatus.ALLOW, metric=metric, unit=unit)
        if (requested_integer is None) == (requested_decimal is None):
            return BudgetDecision(
                status=BudgetDecisionStatus.DENY_INVALID_REQUEST,
                metric=metric,
                unit=unit,
                reason_code="invalid_reservation_value",
            )
        if limit.currency != currency:
            return BudgetDecision(
                status=BudgetDecisionStatus.DENY_INVALID_REQUEST,
                metric=metric,
                unit=unit,
                reason_code="currency_mismatch",
            )
        records = self._repository.list_usage_records(run_id)
        reservations = self._repository.list_reservations(run_id)
        if any(
            item.status is ReservationStatus.UNRESOLVED and item.metric is metric
            for item in reservations
        ):
            return BudgetDecision(
                status=BudgetDecisionStatus.DENY_UNRESOLVED_RESERVATION,
                metric=metric,
                unit=unit,
                reason_code="unresolved_reservation",
            )
        if require_known and any(
            item.provenance is UsageProvenance.UNAVAILABLE
            and _budget_metric_for_usage(item.metric) is metric
            for item in records
        ):
            return BudgetDecision(
                status=BudgetDecisionStatus.DENY_USAGE_UNAVAILABLE,
                metric=metric,
                unit=unit,
                reason_code="usage_unavailable",
            )
        if any(
            item.reason_code == "validation_duration_overage"
            and _budget_metric_for_usage(item.metric) is metric
            for item in records
        ):
            return BudgetDecision(
                status=BudgetDecisionStatus.DENY_LIMIT_EXHAUSTED,
                metric=metric,
                unit=unit,
                reason_code="validation_duration_overage",
            )
        if limit.integer_limit is not None:
            consumed = sum(
                item.integer_value or 0
                for item in records
                if _budget_metric_for_usage(item.metric) is metric
                and item.provenance is not UsageProvenance.UNAVAILABLE
            )
            reserved = sum(
                item.integer_reserved or 0
                for item in reservations
                if item.metric is metric and item.status is ReservationStatus.ACTIVE
            )
            remaining = limit.integer_limit - consumed - reserved
            if requested_integer is None or requested_integer > remaining:
                return BudgetDecision(
                    status=BudgetDecisionStatus.DENY_LIMIT_EXHAUSTED,
                    metric=metric,
                    unit=unit,
                    remaining_integer=max(0, remaining),
                    reason_code="limit_exhausted",
                )
            return BudgetDecision(
                status=BudgetDecisionStatus.ALLOW,
                metric=metric,
                unit=unit,
                remaining_integer=remaining,
            )
        assert limit.decimal_limit is not None
        consumed_decimal = sum(
            (item.decimal_value or Decimal("0"))
            for item in records
            if _budget_metric_for_usage(item.metric) is metric
            and item.provenance is not UsageProvenance.UNAVAILABLE
        )
        reserved_decimal = sum(
            (item.decimal_reserved or Decimal("0"))
            for item in reservations
            if item.metric is metric and item.status is ReservationStatus.ACTIVE
        )
        remaining_decimal = limit.decimal_limit - consumed_decimal - reserved_decimal
        if requested_decimal is None or requested_decimal > remaining_decimal:
            return BudgetDecision(
                status=BudgetDecisionStatus.DENY_LIMIT_EXHAUSTED,
                metric=metric,
                unit=unit,
                remaining_decimal=max(Decimal("0"), remaining_decimal),
                reason_code="limit_exhausted",
            )
        return BudgetDecision(
            status=BudgetDecisionStatus.ALLOW,
            metric=metric,
            unit=unit,
            remaining_decimal=remaining_decimal,
        )

    def reserve(
        self,
        reservation: BudgetReservation,
        policy: BudgetPolicy,
        *,
        expected_revision: int | None = None,
        require_known: bool = False,
    ) -> BudgetDecision:
        return self._repository.reserve_if_allowed(
            reservation,
            policy,
            expected_revision=expected_revision,
            require_known=require_known,
        )

    def settle(
        self,
        reservation: BudgetReservation,
        settlement: BudgetSettlement,
        usage_records: tuple[UsageRecord, ...],
    ) -> bool:
        return self._repository.settle_reservation(reservation, settlement, usage_records)

    def record(self, usage_records: tuple[UsageRecord, ...]) -> int:
        """Append bounded non-reservation usage with canonical retry semantics."""
        return sum(self._repository.record_usage(record) for record in usage_records)

    def usage_records(self, run_id: RunId) -> tuple[UsageRecord, ...]:
        """Return the bounded durable usage evidence for reconciliation."""
        return self._repository.list_usage_records(run_id)

    def reservations(self, run_id: RunId) -> tuple[BudgetReservation, ...]:
        """Return reservations with their persisted settlement status projected."""
        return self._repository.list_reservations(run_id)

    def mark_unresolved(
        self,
        reservation: BudgetReservation,
        *,
        observed_at: datetime,
        reason_code: str,
    ) -> bool:
        """Durably preserve capacity when launch or completion cannot be proven."""
        settlement = BudgetSettlement(
            reservation_id=reservation.id,
            settled_at=observed_at,
            status=ReservationStatus.UNRESOLVED,
            reason_code=reason_code,
        )
        return self._repository.settle_reservation(reservation, settlement, ())


def context_usage_records(
    manifest: ContextManifest, *, observed_at: datetime
) -> tuple[UsageRecord, ...]:
    values = (
        (UsageMetric.CONTEXT_BASELINE_BYTES, manifest.baseline_bytes),
        (UsageMetric.CONTEXT_RETAINED_BYTES, manifest.retained_bytes),
        (UsageMetric.CONTEXT_EXCLUDED_BYTES, manifest.excluded_bytes),
        (UsageMetric.CONTEXT_TRUNCATED_BYTES, manifest.truncated_bytes),
        (UsageMetric.CONTEXT_DUPLICATE_BYTES, manifest.duplicate_bytes_avoided),
        (UsageMetric.CONTEXT_ITEM_COUNT, manifest.included_count),
    )
    return tuple(
        UsageRecord(
            id=usage_record_id(manifest.run_id, f"{manifest.manifest_id}:{metric.value}", metric),
            run_id=manifest.run_id,
            work_package_id=manifest.work_package_id,
            metric=metric,
            unit=UsageUnit.BYTES
            if metric is not UsageMetric.CONTEXT_ITEM_COUNT
            else UsageUnit.COMMANDS,
            provenance=UsageProvenance.MEASURED,
            source=UsageSource.CONTEXT,
            observed_at=observed_at,
            correlation_key=f"{manifest.manifest_id}:{metric.value}",
            integer_value=value,
        )
        for metric, value in values
    )


def provider_usage_records(response: AgentResponse) -> tuple[UsageRecord, ...]:
    base = dict(
        run_id=response.run_id,
        work_package_id=response.work_package_id,
        source=UsageSource.AGENT,
        observed_at=response.completed_at,
        attempt_id=response.attempt_id,
        invocation_id=response.invocation_id,
        provider_id=response.identity.provider_id.root,
        adapter_id=response.identity.adapter_id.root,
        model=response.identity.model,
    )
    usage = response.usage
    values = (
        (
            (UsageMetric.INPUT_TOKENS, None),
            (UsageMetric.OUTPUT_TOKENS, None),
            (UsageMetric.TOTAL_TOKENS, None),
        )
        if usage is None
        else (
            (UsageMetric.INPUT_TOKENS, usage.input_tokens),
            (UsageMetric.OUTPUT_TOKENS, usage.output_tokens),
            (UsageMetric.TOTAL_TOKENS, usage.total_tokens),
        )
    )
    records = []
    for metric, value in values:
        correlation = f"{response.invocation_id.root}:{metric.value}"
        records.append(
            UsageRecord.model_validate(
                {
                    **base,
                    "id": usage_record_id(response.run_id, correlation, metric),
                    "metric": metric,
                    "unit": UsageUnit.TOKENS,
                    "provenance": (
                        UsageProvenance.PROVIDER_REPORTED
                        if value is not None
                        else UsageProvenance.UNAVAILABLE
                    ),
                    "correlation_key": correlation,
                    "integer_value": value,
                    "reason_code": None if value is not None else "provider_usage_missing",
                }
            )
        )
    cost_correlation = f"{response.invocation_id.root}:{UsageMetric.ESTIMATED_COST.value}"
    records.append(
        UsageRecord.model_validate(
            {
                **base,
                "id": usage_record_id(
                    response.run_id, cost_correlation, UsageMetric.ESTIMATED_COST
                ),
                "metric": UsageMetric.ESTIMATED_COST,
                "unit": UsageUnit.DECIMAL_CURRENCY,
                "provenance": UsageProvenance.UNAVAILABLE,
                "correlation_key": cost_correlation,
                "reason_code": "cost_estimator_unavailable",
            }
        )
    )
    return tuple(records)


def _unit(metric: BudgetMetric) -> UsageUnit:
    units: dict[BudgetMetric, UsageUnit] = {
        BudgetMetric.BUILD_ATTEMPTS: UsageUnit.ATTEMPTS,
        BudgetMetric.REVIEW_ATTEMPTS: UsageUnit.ATTEMPTS,
        BudgetMetric.REPAIR_ATTEMPTS: UsageUnit.ATTEMPTS,
        BudgetMetric.TOTAL_DURATION: UsageUnit.MILLISECONDS,
        BudgetMetric.REMOTE_TOKENS: UsageUnit.TOKENS,
        BudgetMetric.ESTIMATED_COST: UsageUnit.DECIMAL_CURRENCY,
    }
    return units[metric]


def _budget_metric_for_usage(metric: UsageMetric) -> BudgetMetric | None:
    return {
        UsageMetric.BUILD_ATTEMPTS: BudgetMetric.BUILD_ATTEMPTS,
        UsageMetric.REVIEW_ATTEMPTS: BudgetMetric.REVIEW_ATTEMPTS,
        UsageMetric.REPAIR_ATTEMPTS: BudgetMetric.REPAIR_ATTEMPTS,
        UsageMetric.LOCAL_ATTEMPTS: BudgetMetric.BUILD_ATTEMPTS,
        UsageMetric.REMOTE_ATTEMPTS: BudgetMetric.BUILD_ATTEMPTS,
        UsageMetric.PROVIDER_DURATION: BudgetMetric.TOTAL_DURATION,
        UsageMetric.COMMAND_DURATION: BudgetMetric.TOTAL_DURATION,
        UsageMetric.VALIDATION_DURATION: BudgetMetric.TOTAL_DURATION,
        UsageMetric.TOTAL_TOKENS: BudgetMetric.REMOTE_TOKENS,
        UsageMetric.ESTIMATED_COST: BudgetMetric.ESTIMATED_COST,
    }.get(metric)
