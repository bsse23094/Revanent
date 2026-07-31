"""Strict provider-neutral telemetry, budget, and reservation contracts."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from revanent.domain import RunId, WorkPackageId
from revanent.domain.identifiers import AgentAttemptId, AgentInvocationId

TELEMETRY_SCHEMA_VERSION: Literal[1] = 1
MAX_USAGE_VALUE = 1_000_000_000_000


class _TelemetryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, str_strip_whitespace=True)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("telemetry timestamps must be timezone-aware UTC")
    return value


class UsageProvenance(StrEnum):
    MEASURED = "MEASURED"
    PROVIDER_REPORTED = "PROVIDER_REPORTED"
    ESTIMATED = "ESTIMATED"
    UNAVAILABLE = "UNAVAILABLE"


class UsageUnit(StrEnum):
    BYTES = "BYTES"
    MILLISECONDS = "MILLISECONDS"
    TOKENS = "TOKENS"
    ATTEMPTS = "ATTEMPTS"
    COMMANDS = "COMMANDS"
    INVOCATIONS = "INVOCATIONS"
    DECIMAL_CURRENCY = "DECIMAL_CURRENCY"


class UsageMetric(StrEnum):
    CONTEXT_BASELINE_BYTES = "CONTEXT_BASELINE_BYTES"
    CONTEXT_RETAINED_BYTES = "CONTEXT_RETAINED_BYTES"
    CONTEXT_EXCLUDED_BYTES = "CONTEXT_EXCLUDED_BYTES"
    CONTEXT_TRUNCATED_BYTES = "CONTEXT_TRUNCATED_BYTES"
    CONTEXT_DUPLICATE_BYTES = "CONTEXT_DUPLICATE_BYTES"
    CONTEXT_ITEM_COUNT = "CONTEXT_ITEM_COUNT"
    COMMAND_DURATION = "COMMAND_DURATION"
    COMMAND_COUNT = "COMMAND_COUNT"
    VALIDATION_DURATION = "VALIDATION_DURATION"
    PROVIDER_DURATION = "PROVIDER_DURATION"
    INPUT_TOKENS = "INPUT_TOKENS"
    CACHED_INPUT_TOKENS = "CACHED_INPUT_TOKENS"
    OUTPUT_TOKENS = "OUTPUT_TOKENS"
    REASONING_TOKENS = "REASONING_TOKENS"
    TOTAL_TOKENS = "TOTAL_TOKENS"
    BUILD_ATTEMPTS = "BUILD_ATTEMPTS"
    REVIEW_ATTEMPTS = "REVIEW_ATTEMPTS"
    REPAIR_ATTEMPTS = "REPAIR_ATTEMPTS"
    LOCAL_ATTEMPTS = "LOCAL_ATTEMPTS"
    REMOTE_ATTEMPTS = "REMOTE_ATTEMPTS"
    PROVIDER_INVOCATIONS = "PROVIDER_INVOCATIONS"
    ESTIMATED_COST = "ESTIMATED_COST"


class UsageSource(StrEnum):
    CONTEXT = "CONTEXT"
    COMMAND = "COMMAND"
    VALIDATION = "VALIDATION"
    AGENT = "AGENT"
    ORCHESTRATION = "ORCHESTRATION"


_USAGE_METRIC_UNITS = {
    UsageMetric.CONTEXT_BASELINE_BYTES: UsageUnit.BYTES,
    UsageMetric.CONTEXT_RETAINED_BYTES: UsageUnit.BYTES,
    UsageMetric.CONTEXT_EXCLUDED_BYTES: UsageUnit.BYTES,
    UsageMetric.CONTEXT_TRUNCATED_BYTES: UsageUnit.BYTES,
    UsageMetric.CONTEXT_DUPLICATE_BYTES: UsageUnit.BYTES,
    UsageMetric.CONTEXT_ITEM_COUNT: UsageUnit.COMMANDS,
    UsageMetric.COMMAND_DURATION: UsageUnit.MILLISECONDS,
    UsageMetric.COMMAND_COUNT: UsageUnit.COMMANDS,
    UsageMetric.VALIDATION_DURATION: UsageUnit.MILLISECONDS,
    UsageMetric.PROVIDER_DURATION: UsageUnit.MILLISECONDS,
    UsageMetric.INPUT_TOKENS: UsageUnit.TOKENS,
    UsageMetric.CACHED_INPUT_TOKENS: UsageUnit.TOKENS,
    UsageMetric.OUTPUT_TOKENS: UsageUnit.TOKENS,
    UsageMetric.REASONING_TOKENS: UsageUnit.TOKENS,
    UsageMetric.TOTAL_TOKENS: UsageUnit.TOKENS,
    UsageMetric.BUILD_ATTEMPTS: UsageUnit.ATTEMPTS,
    UsageMetric.REVIEW_ATTEMPTS: UsageUnit.ATTEMPTS,
    UsageMetric.REPAIR_ATTEMPTS: UsageUnit.ATTEMPTS,
    UsageMetric.LOCAL_ATTEMPTS: UsageUnit.ATTEMPTS,
    UsageMetric.REMOTE_ATTEMPTS: UsageUnit.ATTEMPTS,
    UsageMetric.PROVIDER_INVOCATIONS: UsageUnit.INVOCATIONS,
    UsageMetric.ESTIMATED_COST: UsageUnit.DECIMAL_CURRENCY,
}


class ReservationStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SETTLED = "SETTLED"
    UNRESOLVED = "UNRESOLVED"


class BudgetDecisionStatus(StrEnum):
    ALLOW = "ALLOW"
    DENY_LIMIT_EXHAUSTED = "DENY_LIMIT_EXHAUSTED"
    DENY_USAGE_UNAVAILABLE = "DENY_USAGE_UNAVAILABLE"
    DENY_UNRESOLVED_RESERVATION = "DENY_UNRESOLVED_RESERVATION"
    DENY_INVALID_REQUEST = "DENY_INVALID_REQUEST"
    DENY_STALE_STATE = "DENY_STALE_STATE"


class BudgetMetric(StrEnum):
    BUILD_ATTEMPTS = "BUILD_ATTEMPTS"
    REVIEW_ATTEMPTS = "REVIEW_ATTEMPTS"
    REPAIR_ATTEMPTS = "REPAIR_ATTEMPTS"
    TOTAL_DURATION = "TOTAL_DURATION"
    REMOTE_TOKENS = "REMOTE_TOKENS"
    ESTIMATED_COST = "ESTIMATED_COST"


_METRIC_UNITS = {
    BudgetMetric.BUILD_ATTEMPTS: UsageUnit.ATTEMPTS,
    BudgetMetric.REVIEW_ATTEMPTS: UsageUnit.ATTEMPTS,
    BudgetMetric.REPAIR_ATTEMPTS: UsageUnit.ATTEMPTS,
    BudgetMetric.TOTAL_DURATION: UsageUnit.MILLISECONDS,
    BudgetMetric.REMOTE_TOKENS: UsageUnit.TOKENS,
    BudgetMetric.ESTIMATED_COST: UsageUnit.DECIMAL_CURRENCY,
}


class BudgetLimit(_TelemetryModel):
    metric: BudgetMetric
    unit: UsageUnit
    integer_limit: int | None = Field(default=None, ge=1, le=MAX_USAGE_VALUE)
    decimal_limit: Decimal | None = Field(default=None, gt=Decimal("0"), max_digits=18)
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] | None = None

    @model_validator(mode="after")
    def _valid(self) -> Self:
        if self.unit is not _METRIC_UNITS[self.metric]:
            raise ValueError("budget metric and unit do not match")
        if (self.integer_limit is None) == (self.decimal_limit is None):
            raise ValueError("budget limit requires exactly one numeric value")
        if self.unit is UsageUnit.DECIMAL_CURRENCY:
            if self.decimal_limit is None or self.currency is None:
                raise ValueError("currency budget requires Decimal value and currency")
        elif self.currency is not None or self.decimal_limit is not None:
            raise ValueError("non-currency budget cannot carry currency or Decimal value")
        return self


class BudgetPolicy(_TelemetryModel):
    schema_version: Literal[1] = TELEMETRY_SCHEMA_VERSION
    limits: tuple[BudgetLimit, ...] = ()

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        keys = [item.metric.value for item in self.limits]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ValueError("budget limits must be sorted and unique by metric")
        return self


class UsageRecord(_TelemetryModel):
    schema_version: Literal[1] = TELEMETRY_SCHEMA_VERSION
    id: Annotated[str, Field(pattern=r"^usage_[0-9a-f]{64}$")]
    run_id: RunId
    work_package_id: WorkPackageId
    metric: UsageMetric
    unit: UsageUnit
    provenance: UsageProvenance
    source: UsageSource
    observed_at: datetime
    correlation_key: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
    integer_value: int | None = Field(default=None, ge=0, le=MAX_USAGE_VALUE)
    decimal_value: Decimal | None = Field(default=None, ge=Decimal("0"), max_digits=18)
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] | None = None
    attempt_id: AgentAttemptId | None = None
    invocation_id: AgentInvocationId | None = None
    provider_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")] | None = None
    adapter_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")] | None = None
    model: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    estimator_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")] | None = (
        None
    )
    reason_code: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")] | None = None

    _observed_utc = field_validator("observed_at")(_utc)

    @model_validator(mode="after")
    def _valid(self) -> Self:
        if self.unit is not _USAGE_METRIC_UNITS[self.metric]:
            raise ValueError("usage metric and unit do not match")
        if self.provenance is UsageProvenance.UNAVAILABLE:
            if (
                self.integer_value is not None
                or self.decimal_value is not None
                or self.reason_code is None
            ):
                raise ValueError("unavailable usage requires a reason and no numeric value")
            if self.currency is not None or self.estimator_id is not None:
                raise ValueError("unavailable usage cannot claim currency or an estimator")
            return self
        if (self.integer_value is None) == (self.decimal_value is None):
            raise ValueError("available usage requires exactly one numeric value")
        if self.unit is UsageUnit.DECIMAL_CURRENCY:
            if self.decimal_value is None or self.currency is None:
                raise ValueError("currency usage requires Decimal value and currency")
            if self.provenance is not UsageProvenance.ESTIMATED:
                raise ValueError("currency usage must be explicitly estimated")
            if self.estimator_id is None:
                raise ValueError("estimated currency requires an estimator identity")
        elif (
            self.decimal_value is not None
            or self.currency is not None
            or self.estimator_id is not None
        ):
            raise ValueError("non-currency usage cannot carry decimal, currency, or estimator")
        if (
            self.provenance is UsageProvenance.PROVIDER_REPORTED
            and self.source is not UsageSource.AGENT
        ):
            raise ValueError("provider-reported usage must originate from an agent response")
        return self


class BudgetReservation(_TelemetryModel):
    schema_version: Literal[1] = TELEMETRY_SCHEMA_VERSION
    id: Annotated[str, Field(pattern=r"^reserve_[0-9a-f]{64}$")]
    run_id: RunId
    work_package_id: WorkPackageId
    metric: BudgetMetric
    unit: UsageUnit
    operation: Annotated[str, Field(pattern=r"^[A-Z][A-Z_]{1,63}$")]
    idempotency_key: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
    created_at: datetime
    integer_reserved: int | None = Field(default=None, ge=1, le=MAX_USAGE_VALUE)
    decimal_reserved: Decimal | None = Field(default=None, gt=Decimal("0"), max_digits=18)
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] | None = None
    attempt_id: AgentAttemptId | None = None
    invocation_id: AgentInvocationId | None = None
    status: ReservationStatus = ReservationStatus.ACTIVE

    _created_utc = field_validator("created_at")(_utc)

    @model_validator(mode="after")
    def _valid(self) -> Self:
        if self.unit is not _METRIC_UNITS[self.metric]:
            raise ValueError("reservation metric and unit do not match")
        if (self.integer_reserved is None) == (self.decimal_reserved is None):
            raise ValueError("reservation requires exactly one numeric value")
        if self.unit is UsageUnit.DECIMAL_CURRENCY:
            if self.decimal_reserved is None or self.currency is None:
                raise ValueError("currency reservation requires Decimal value and currency")
        elif self.decimal_reserved is not None or self.currency is not None:
            raise ValueError("non-currency reservation cannot carry Decimal value or currency")
        return self


class BudgetSettlement(_TelemetryModel):
    schema_version: Literal[1] = TELEMETRY_SCHEMA_VERSION
    reservation_id: Annotated[str, Field(pattern=r"^reserve_[0-9a-f]{64}$")]
    settled_at: datetime
    integer_consumed: int | None = Field(default=None, ge=0, le=MAX_USAGE_VALUE)
    decimal_consumed: Decimal | None = Field(default=None, ge=Decimal("0"), max_digits=18)
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] | None = None
    status: Literal[ReservationStatus.SETTLED, ReservationStatus.UNRESOLVED]
    reason_code: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")] | None = None

    _settled_utc = field_validator("settled_at")(_utc)

    @model_validator(mode="after")
    def _valid(self) -> Self:
        if self.status is ReservationStatus.UNRESOLVED:
            if (
                self.integer_consumed is not None
                or self.decimal_consumed is not None
                or self.currency is not None
                or self.reason_code is None
            ):
                raise ValueError("unresolved settlement requires a reason and no consumption")
        elif (self.integer_consumed is None) == (self.decimal_consumed is None):
            raise ValueError("settled reservation requires exactly one consumed value")
        if self.decimal_consumed is not None:
            if self.currency is None:
                raise ValueError("Decimal settlement requires a currency")
        elif self.currency is not None:
            raise ValueError("integer settlement cannot carry a currency")
        return self


class BudgetDecision(_TelemetryModel):
    schema_version: Literal[1] = TELEMETRY_SCHEMA_VERSION
    status: BudgetDecisionStatus
    metric: BudgetMetric
    unit: UsageUnit
    remaining_integer: int | None = Field(default=None, ge=0, le=MAX_USAGE_VALUE)
    remaining_decimal: Decimal | None = Field(default=None, ge=Decimal("0"), max_digits=18)
    reason_code: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")] | None = None


class TelemetrySnapshot(_TelemetryModel):
    schema_version: Literal[1] = TELEMETRY_SCHEMA_VERSION
    records: tuple[UsageRecord, ...] = ()
    active_reservations: tuple[BudgetReservation, ...] = ()
    unresolved_reservations: tuple[BudgetReservation, ...] = ()


class TelemetryRepository(Protocol):
    def list_usage_records(self, run_id: RunId) -> tuple[UsageRecord, ...]: ...
    def list_reservations(self, run_id: RunId) -> tuple[BudgetReservation, ...]: ...
    def record_usage(self, record: UsageRecord) -> bool: ...
    def reserve(self, reservation: BudgetReservation) -> bool: ...

    def reserve_if_allowed(
        self,
        reservation: BudgetReservation,
        policy: BudgetPolicy,
        *,
        expected_revision: int | None = None,
        require_known: bool = False,
    ) -> BudgetDecision: ...

    def settle(self, settlement: BudgetSettlement) -> bool: ...

    def settle_reservation(
        self,
        reservation: BudgetReservation,
        settlement: BudgetSettlement,
        usage_records: tuple[UsageRecord, ...],
    ) -> bool: ...


def usage_record_id(run_id: RunId, correlation_key: str, metric: UsageMetric) -> str:
    return (
        "usage_"
        + hashlib.sha256(f"{run_id.root}:{correlation_key}:{metric.value}".encode()).hexdigest()
    )


def reservation_id(run_id: RunId, idempotency_key: str, metric: BudgetMetric) -> str:
    return (
        "reserve_"
        + hashlib.sha256(f"{run_id.root}:{idempotency_key}:{metric.value}".encode()).hexdigest()
    )


def canonical_telemetry_bytes(value: _TelemetryModel) -> bytes:
    return json.dumps(
        value.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
