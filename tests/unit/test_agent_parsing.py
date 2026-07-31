"""Adversarial tests for strict untrusted agent-output normalization."""

from __future__ import annotations

import json

import pytest

from revanent.agents.base import (
    AgentOutputError,
    canonical_json_bytes,
    normalize_agent_output,
    parse_agent_response_envelope,
    validate_agent_response_correlation,
)
from revanent.ports.agents import (
    AgentFailureCategory,
    AgentOutputLimits,
    AgentRole,
    AgentStatus,
)
from tests.agent_factories import NOW, make_identity, make_request, make_response


def test_valid_response_parses_and_correlates_exactly() -> None:
    request = make_request()
    response = make_response()
    parsed = parse_agent_response_envelope(canonical_json_bytes(response))
    validate_agent_response_correlation(parsed, request)
    assert parsed == response


def test_correlation_mismatch_normalizes_to_invalid_output() -> None:
    request = make_request()
    raw = json.loads(canonical_json_bytes(make_response()))
    raw["invocation_id"] = f"inv_{'9' * 32}"
    result = normalize_agent_output(
        _json_bytes(raw),
        request,
        identity=make_identity(),
        started_at=NOW,
        completed_at=NOW,
    )
    assert result.status is AgentStatus.INVALID_OUTPUT
    assert result.failure is not None
    assert result.failure.category is AgentFailureCategory.CORRELATION_MISMATCH
    assert result.invocation_id == request.invocation_id


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (b"\xff", "invalid_utf8"),
        (b'{"schema_version":1} trailing', "malformed_json"),
        (b'{"schema_version":1,"schema_version":1}', "duplicate_json_key"),
        (b'{"schema_version":1,"duration_ms":NaN}', "nonstandard_number"),
        (b"[]", "invalid_envelope_type"),
    ],
)
def test_malformed_bytes_fail_deterministically(raw: bytes, code: str) -> None:
    with pytest.raises(AgentOutputError) as captured:
        parse_agent_response_envelope(raw)
    assert captured.value.code == code
    assert raw.decode("utf-8", errors="replace") not in str(captured.value)


def test_oversized_output_is_rejected_before_parsing() -> None:
    raw = b"{" + b"x" * 32 + b"}"
    with pytest.raises(AgentOutputError) as captured:
        parse_agent_response_envelope(raw, limits=AgentOutputLimits(max_input_bytes=8))
    assert captured.value.code == "output_too_large"


def test_excessive_depth_and_collection_sizes_are_rejected() -> None:
    nested: object = "end"
    for _ in range(5):
        nested = [nested]
    with pytest.raises(AgentOutputError) as captured:
        parse_agent_response_envelope(
            _json_bytes({"schema_version": 1, "value": nested}),
            limits=AgentOutputLimits(max_depth=3),
        )
    assert captured.value.code == "json_depth_limit"

    with pytest.raises(AgentOutputError) as captured:
        parse_agent_response_envelope(
            _json_bytes({"schema_version": 1, "value": [1, 2, 3]}),
            limits=AgentOutputLimits(max_collection_items=2),
        )
    assert captured.value.code == "json_collection_limit"


def test_unknown_version_field_enum_and_missing_field_are_invalid_output() -> None:
    cases: list[dict[str, object]] = []
    base = json.loads(canonical_json_bytes(make_response()))
    for key, value in (
        ("schema_version", 2),
        ("status", "FRIENDLY_SUCCESS"),
    ):
        data = dict(base)
        data[key] = value
        cases.append(data)
    unknown = dict(base)
    unknown["approval"] = True
    cases.append(unknown)
    missing = dict(base)
    del missing["run_id"]
    cases.append(missing)

    for data in cases:
        result = normalize_agent_output(
            _json_bytes(data),
            make_request(),
            identity=make_identity(),
            started_at=NOW,
            completed_at=NOW,
        )
        assert result.status is AgentStatus.INVALID_OUTPUT
        assert result.payload is None


def test_failed_provider_output_cannot_masquerade_as_success() -> None:
    data = json.loads(canonical_json_bytes(make_response()))
    data["status"] = "FAILED"
    result = normalize_agent_output(
        _json_bytes(data),
        make_request(),
        identity=make_identity(),
        started_at=NOW,
        completed_at=NOW,
    )
    assert result.status is AgentStatus.INVALID_OUTPUT
    assert not result.succeeded


def test_configured_secret_is_redacted_from_success_and_failures() -> None:
    secret = "fixture-secret-value"
    response = make_response().model_dump(mode="json")
    response["public_text"] = f"provider leaked {secret}"
    parsed = parse_agent_response_envelope(_json_bytes(response), sensitive_values=(secret,))
    assert secret not in parsed.public_text
    assert "[REDACTED]" in parsed.public_text

    malformed = _json_bytes({"schema_version": 1, "secret": secret})
    result = normalize_agent_output(
        malformed,
        make_request(),
        identity=make_identity(),
        started_at=NOW,
        completed_at=NOW,
        sensitive_values=(secret,),
    )
    assert secret not in result.model_dump_json()
    assert secret not in str(result.failure)


def test_role_specific_payload_mismatch_is_rejected() -> None:
    request = make_request(AgentRole.REVIEWER)
    response = make_response(AgentRole.REVIEWER).model_dump(mode="json")
    response["role"] = "BUILDER"
    result = normalize_agent_output(
        _json_bytes(response),
        request,
        identity=make_identity(),
        started_at=NOW,
        completed_at=NOW,
    )
    assert result.status is AgentStatus.INVALID_OUTPUT


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
