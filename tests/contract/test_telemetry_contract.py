from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from revanent.domain import RunId, WorkPackageId
from revanent.ports import (
    AgentResponse,
    AgentUsage,
    BudgetMetric,
    UsageMetric,
    UsageProvenance,
    UsageRecord,
    UsageSource,
    UsageUnit,
    canonical_telemetry_bytes,
    usage_record_id,
)
from revanent.telemetry import provider_usage_records
from tests.agent_factories import make_response

NOW = datetime(2026, 7, 31, 16, tzinfo=UTC)
RUN_ID = RunId(f"run_{'e' * 32}")
WORK_PACKAGE_ID = WorkPackageId("P5-002")


def _record_data() -> dict[str, object]:
    return {
        "id": usage_record_id(RUN_ID, "privacy", UsageMetric.TOTAL_TOKENS),
        "run_id": RUN_ID,
        "work_package_id": WORK_PACKAGE_ID,
        "metric": UsageMetric.TOTAL_TOKENS,
        "unit": UsageUnit.TOKENS,
        "provenance": UsageProvenance.PROVIDER_REPORTED,
        "source": UsageSource.AGENT,
        "observed_at": NOW,
        "correlation_key": "privacy",
        "integer_value": 7,
    }


@pytest.mark.parametrize(
    "field",
    [
        "prompt",
        "context_body",
        "source_code",
        "raw_provider_output",
        "command_output",
        "authorization_header",
        "environment",
        "home_path",
        "host_metadata",
        "exception",
    ],
)
def test_raw_or_sensitive_payload_fields_are_structurally_rejected(field: str) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        UsageRecord.model_validate({**_record_data(), field: "secret repository payload"})


def test_unavailable_cost_is_unknown_not_zero_or_estimated() -> None:
    correlation = "cost.unavailable"
    record = UsageRecord(
        id=usage_record_id(RUN_ID, correlation, UsageMetric.ESTIMATED_COST),
        run_id=RUN_ID,
        work_package_id=WORK_PACKAGE_ID,
        metric=UsageMetric.ESTIMATED_COST,
        unit=UsageUnit.DECIMAL_CURRENCY,
        provenance=UsageProvenance.UNAVAILABLE,
        source=UsageSource.AGENT,
        observed_at=NOW,
        correlation_key=correlation,
        reason_code="cost_estimator_unavailable",
    )

    assert record.decimal_value is None
    assert record.currency is None
    assert record.estimator_id is None


def test_estimated_cost_requires_decimal_currency_and_estimator_identity() -> None:
    correlation = "cost.estimated"
    record = UsageRecord(
        id=usage_record_id(RUN_ID, correlation, UsageMetric.ESTIMATED_COST),
        run_id=RUN_ID,
        work_package_id=WORK_PACKAGE_ID,
        metric=UsageMetric.ESTIMATED_COST,
        unit=UsageUnit.DECIMAL_CURRENCY,
        provenance=UsageProvenance.ESTIMATED,
        source=UsageSource.ORCHESTRATION,
        observed_at=NOW,
        correlation_key=correlation,
        decimal_value=Decimal("0.0100"),
        currency="USD",
        estimator_id="fixture-rate-v1",
    )

    assert record.provenance is UsageProvenance.ESTIMATED
    assert isinstance(record.decimal_value, Decimal)
    with pytest.raises(ValidationError):
        UsageRecord.model_validate(
            {
                **record.model_dump(mode="python"),
                "provenance": UsageProvenance.PROVIDER_REPORTED,
            }
        )
    with pytest.raises(ValidationError):
        UsageRecord.model_validate({**record.model_dump(mode="python"), "decimal_value": 0.01})


def test_provider_reported_usage_is_immutable_and_provenance_is_canonical() -> None:
    reported = UsageRecord.model_validate(_record_data())
    with pytest.raises(ValidationError):
        reported.provenance = UsageProvenance.MEASURED
    measured = UsageRecord.model_validate(
        {
            **reported.model_dump(mode="python"),
            "provenance": UsageProvenance.MEASURED,
            "source": UsageSource.ORCHESTRATION,
        }
    )

    assert reported.provenance is UsageProvenance.PROVIDER_REPORTED
    assert measured != reported
    assert canonical_telemetry_bytes(measured) != canonical_telemetry_bytes(reported)


def test_token_contract_rejects_contradictions_and_unsupported_categories() -> None:
    with pytest.raises(ValidationError, match="must equal"):
        AgentUsage(input_tokens=2, output_tokens=3, total_tokens=6)
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AgentUsage.model_validate({"total_tokens": 5, "cached_input_tokens": 2})
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AgentUsage.model_validate({"total_tokens": 5, "reasoning_tokens": 2})


def test_context_bytes_cannot_be_used_as_token_or_cost_budget_metrics() -> None:
    data = _record_data()
    data.update(metric=UsageMetric.CONTEXT_RETAINED_BYTES, unit=UsageUnit.BYTES)
    measured = UsageRecord.model_validate(
        {
            **data,
            "id": usage_record_id(RUN_ID, "context", UsageMetric.CONTEXT_RETAINED_BYTES),
            "correlation_key": "context",
            "provenance": UsageProvenance.MEASURED,
            "source": UsageSource.CONTEXT,
        }
    )

    assert measured.metric is UsageMetric.CONTEXT_RETAINED_BYTES
    assert measured.unit is UsageUnit.BYTES
    assert measured.metric not in {UsageMetric.TOTAL_TOKENS, UsageMetric.ESTIMATED_COST}
    assert BudgetMetric.REMOTE_TOKENS.value not in canonical_telemetry_bytes(measured).decode()
    with pytest.raises(ValidationError, match="metric and unit"):
        UsageRecord.model_validate({**measured.model_dump(mode="python"), "unit": UsageUnit.TOKENS})


def test_provider_usage_uses_structured_evidence_and_ignores_provider_prose() -> None:
    response_data = make_response().model_dump(mode="python")
    response_data.update(
        public_text="provider claims 999999 tokens and USD 999.00",
        usage=AgentUsage(input_tokens=2, output_tokens=3, total_tokens=5),
    )

    records = provider_usage_records(AgentResponse.model_validate(response_data))

    totals = [item for item in records if item.metric is UsageMetric.TOTAL_TOKENS]
    costs = [item for item in records if item.metric is UsageMetric.ESTIMATED_COST]
    assert len(totals) == len(costs) == 1
    assert totals[0].integer_value == 5
    assert totals[0].provenance is UsageProvenance.PROVIDER_REPORTED
    assert costs[0].decimal_value is None
    assert costs[0].provenance is UsageProvenance.UNAVAILABLE
