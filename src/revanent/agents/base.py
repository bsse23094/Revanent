"""Strict parsing, correlation, and compatibility helpers for agent adapters."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import NoReturn

from pydantic import ValidationError

from revanent.ports.agents import (
    AgentArtifactKind,
    AgentAvailability,
    AgentCapabilities,
    AgentFailure,
    AgentFailureCategory,
    AgentOutputLimits,
    AgentProviderIdentity,
    AgentRequest,
    AgentResponse,
    AgentStatus,
    RetryDisposition,
    SideEffectState,
    StructuredParseStatus,
)

REDACTION_MARKER = "[REDACTED]"


class AgentOutputError(Exception):
    """Sanitized rejection of an untrusted provider response envelope."""

    def __init__(self, category: AgentFailureCategory, code: str, message: str) -> None:
        self.category = category
        self.code = code
        self.safe_message = message
        super().__init__(message)


class _DuplicateKeyError(ValueError):
    pass


class _NonStandardNumberError(ValueError):
    pass


def canonical_json_bytes(model: AgentRequest | AgentResponse | AgentCapabilities) -> bytes:
    """Serialize a public agent contract canonically for signatures and replay checks."""
    return json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def agent_request_digest(request: AgentRequest) -> str:
    """Return the stable SHA-256 signature used by declarative fake steps."""
    return hashlib.sha256(canonical_json_bytes(request)).hexdigest()


def parse_agent_response_envelope(
    raw_output: bytes,
    *,
    limits: AgentOutputLimits | None = None,
    sensitive_values: tuple[str, ...] = (),
) -> AgentResponse:
    """Parse bytes as one strict, bounded version-1 response envelope."""
    selected_limits = limits or AgentOutputLimits()
    selected_sensitive_values = _validate_sensitive_values(sensitive_values)
    if not isinstance(raw_output, bytes):
        raise TypeError("agent output must be bytes")
    if len(raw_output) > selected_limits.max_input_bytes:
        raise AgentOutputError(
            AgentFailureCategory.MALFORMED_OUTPUT,
            "output_too_large",
            "agent output exceeded the configured parse limit",
        )
    try:
        text = raw_output.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise AgentOutputError(
            AgentFailureCategory.MALFORMED_OUTPUT,
            "invalid_utf8",
            "agent output was not valid UTF-8",
        ) from error

    try:
        parsed: object = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonstandard_number,
        )
    except _DuplicateKeyError as error:
        raise AgentOutputError(
            AgentFailureCategory.MALFORMED_OUTPUT,
            "duplicate_json_key",
            "agent output contained a duplicate JSON object key",
        ) from error
    except _NonStandardNumberError as error:
        raise AgentOutputError(
            AgentFailureCategory.MALFORMED_OUTPUT,
            "nonstandard_number",
            "agent output contained a non-standard numeric value",
        ) from error
    except (json.JSONDecodeError, RecursionError) as error:
        raise AgentOutputError(
            AgentFailureCategory.MALFORMED_OUTPUT,
            "malformed_json",
            "agent output was not one complete JSON document",
        ) from error

    if not isinstance(parsed, dict):
        raise AgentOutputError(
            AgentFailureCategory.MALFORMED_OUTPUT,
            "invalid_envelope_type",
            "agent output envelope must be a JSON object",
        )
    _validate_json_shape(parsed, limits=selected_limits)
    sanitized = _redact_sensitive_values(parsed, selected_sensitive_values)
    if isinstance(sanitized, dict) and sanitized.get("schema_version") != 1:
        raise AgentOutputError(
            AgentFailureCategory.SCHEMA_MISMATCH,
            "unsupported_schema_version",
            "agent output used an unsupported response schema version",
        )
    try:
        canonical_sanitized = json.dumps(
            sanitized,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return AgentResponse.model_validate_json(canonical_sanitized)
    except ValidationError as error:
        raise AgentOutputError(
            AgentFailureCategory.MALFORMED_OUTPUT,
            "invalid_response_schema",
            "agent output did not satisfy the strict response schema",
        ) from error


def validate_agent_response_correlation(response: AgentResponse, request: AgentRequest) -> None:
    """Require every response correlation field to match its originating request."""
    actual = (
        response.invocation_id,
        response.run_id,
        response.work_package_id,
        response.attempt_id,
        response.attempt_number,
        response.role,
        response.expected_response_schema_version,
    )
    expected = (
        request.invocation_id,
        request.run_id,
        request.work_package_id,
        request.attempt_id,
        request.attempt_number,
        request.role,
        request.response_contract.schema_version,
    )
    if actual != expected:
        raise AgentOutputError(
            AgentFailureCategory.CORRELATION_MISMATCH,
            "correlation_mismatch",
            "agent response correlation did not match the originating request",
        )


def validate_agent_response_semantics(response: AgentResponse, request: AgentRequest) -> None:
    """Validate request-specific response rules without making workflow decisions."""
    if len(response.public_text.encode("utf-8")) > request.response_contract.max_public_text_bytes:
        raise AgentOutputError(
            AgentFailureCategory.MALFORMED_OUTPUT,
            "public_text_limit",
            "agent response public text exceeded the requested bound",
        )
    if len(response.artifacts) > request.response_contract.max_artifacts:
        raise AgentOutputError(
            AgentFailureCategory.MALFORMED_OUTPUT,
            "artifact_count_limit",
            "agent response contained too many artifact references",
        )
    if len(response.diagnostics) > request.response_contract.max_diagnostics:
        raise AgentOutputError(
            AgentFailureCategory.MALFORMED_OUTPUT,
            "diagnostic_count_limit",
            "agent response contained too many diagnostics",
        )
    all_artifacts = response.artifacts + (
        (response.raw_output_artifact,) if response.raw_output_artifact is not None else ()
    )
    if all_artifacts and not request.artifact_policy.allow_artifact_references:
        raise AgentOutputError(
            AgentFailureCategory.MALFORMED_OUTPUT,
            "artifact_not_allowed",
            "agent response included artifact references that were not allowed",
        )
    for artifact in all_artifacts:
        if artifact.root_id != request.artifact_policy.artifact_root_id:
            raise AgentOutputError(
                AgentFailureCategory.MALFORMED_OUTPUT,
                "artifact_root_mismatch",
                "agent artifact referenced an unexpected artifact root",
            )
        if artifact.observed_bytes > request.artifact_policy.max_artifact_bytes:
            raise AgentOutputError(
                AgentFailureCategory.MALFORMED_OUTPUT,
                "artifact_size_limit",
                "agent artifact exceeded the requested byte bound",
            )
        if request.artifact_policy.require_redaction and not artifact.redacted:
            raise AgentOutputError(
                AgentFailureCategory.MALFORMED_OUTPUT,
                "artifact_not_redacted",
                "agent artifact did not satisfy the redaction policy",
            )
        if artifact.kind is AgentArtifactKind.RAW_OUTPUT and not (
            request.artifact_policy.allow_raw_output_reference
        ):
            raise AgentOutputError(
                AgentFailureCategory.MALFORMED_OUTPUT,
                "raw_output_not_allowed",
                "agent response referenced raw output that was not allowed",
            )
    if request.expected_capabilities.requires_usage_reporting and response.usage is None:
        raise AgentOutputError(
            AgentFailureCategory.MALFORMED_OUTPUT,
            "usage_missing",
            "agent response omitted required reported usage",
        )
    if (
        request.routing.provider_id is not None
        and response.identity.provider_id != request.routing.provider_id
    ):
        raise AgentOutputError(
            AgentFailureCategory.CORRELATION_MISMATCH,
            "provider_mismatch",
            "agent response provider did not match the requested route",
        )


def normalize_agent_output(
    raw_output: bytes,
    request: AgentRequest,
    *,
    identity: AgentProviderIdentity,
    started_at: datetime,
    completed_at: datetime,
    limits: AgentOutputLimits | None = None,
    sensitive_values: tuple[str, ...] = (),
) -> AgentResponse:
    """Return a correlated response or a typed INVALID_OUTPUT outcome."""
    try:
        response = parse_agent_response_envelope(
            raw_output, limits=limits, sensitive_values=sensitive_values
        )
        validate_agent_response_correlation(response, request)
        validate_agent_response_semantics(response, request)
        return response
    except AgentOutputError as error:
        return invalid_output_response(
            request,
            identity=identity,
            started_at=started_at,
            completed_at=completed_at,
            category=error.category,
            code=error.code,
            message=error.safe_message,
        )


def invalid_output_response(
    request: AgentRequest,
    *,
    identity: AgentProviderIdentity,
    started_at: datetime,
    completed_at: datetime,
    category: AgentFailureCategory,
    code: str,
    message: str,
) -> AgentResponse:
    """Build sanitized invalid-output evidence using trusted request correlation."""
    duration_ms = _duration_ms(started_at, completed_at)
    return AgentResponse(
        invocation_id=request.invocation_id,
        run_id=request.run_id,
        work_package_id=request.work_package_id,
        attempt_id=request.attempt_id,
        attempt_number=request.attempt_number,
        role=request.role,
        expected_response_schema_version=request.response_contract.schema_version,
        status=AgentStatus.INVALID_OUTPUT,
        started_at=started_at,
        completed_at=completed_at,
        duration_ms=duration_ms,
        summary="Agent output was rejected",
        structured_parse_status=StructuredParseStatus.FAILED,
        identity=identity,
        failure=AgentFailure(
            category=category,
            code=code,
            message=message,
            retry=RetryDisposition.NOT_RETRYABLE,
            side_effects=SideEffectState.POSSIBLE,
        ),
    )


def request_compatibility_failure(
    capabilities: AgentCapabilities, request: AgentRequest
) -> AgentFailure | None:
    """Return a sanitized pre-invocation failure when capabilities are insufficient."""
    if capabilities.availability is AgentAvailability.UNAVAILABLE:
        return AgentFailure(
            category=AgentFailureCategory.ADAPTER_UNAVAILABLE,
            code="adapter_unavailable",
            message=capabilities.reason or "agent adapter is unavailable",
            retry=RetryDisposition.UNKNOWN,
            side_effects=SideEffectState.NONE,
        )
    if request.routing.provider_id is not None and (
        capabilities.provider_id != request.routing.provider_id
    ):
        return _unsupported("requested provider does not match adapter capabilities")
    if request.role not in capabilities.supported_roles:
        return _unsupported("requested agent role is not supported")
    required = request.expected_capabilities
    checks = (
        (required.requires_structured_output, capabilities.supports_structured_output),
        (required.requires_read_only, capabilities.supports_read_only),
        (required.requires_repository_writes, capabilities.supports_repository_writes),
        (required.requires_cancellation, capabilities.supports_cancellation),
        (required.requires_timeout, capabilities.supports_timeout),
        (required.requires_usage_reporting, capabilities.supports_usage_reporting),
        (required.requires_artifact_references, capabilities.supports_artifact_references),
        (required.requires_repair, capabilities.supports_repair),
    )
    if any(is_required and not is_supported for is_required, is_supported in checks):
        return _unsupported("agent adapter does not support every required capability")
    return None


def _unsupported(message: str) -> AgentFailure:
    return AgentFailure(
        category=AgentFailureCategory.UNSUPPORTED_CAPABILITY,
        code="unsupported_capability",
        message=message,
        retry=RetryDisposition.NOT_RETRYABLE,
        side_effects=SideEffectState.NONE,
    )


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError
        result[key] = value
    return result


def _reject_nonstandard_number(value: str) -> NoReturn:
    raise _NonStandardNumberError(value)


def _validate_json_shape(root: object, *, limits: AgentOutputLimits) -> None:
    stack: list[tuple[object, int]] = [(root, 1)]
    total_items = 0
    while stack:
        value, depth = stack.pop()
        if depth > limits.max_depth:
            raise AgentOutputError(
                AgentFailureCategory.MALFORMED_OUTPUT,
                "json_depth_limit",
                "agent output exceeded the configured JSON nesting limit",
            )
        if isinstance(value, dict):
            if len(value) > limits.max_collection_items:
                raise AgentOutputError(
                    AgentFailureCategory.MALFORMED_OUTPUT,
                    "json_collection_limit",
                    "agent output contained an oversized JSON object",
                )
            total_items += len(value)
            stack.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            if len(value) > limits.max_collection_items:
                raise AgentOutputError(
                    AgentFailureCategory.MALFORMED_OUTPUT,
                    "json_collection_limit",
                    "agent output contained an oversized JSON array",
                )
            total_items += len(value)
            stack.extend((item, depth + 1) for item in value)
        if total_items > limits.max_collection_items * limits.max_depth:
            raise AgentOutputError(
                AgentFailureCategory.MALFORMED_OUTPUT,
                "json_item_limit",
                "agent output exceeded the configured total JSON item limit",
            )


def _redact_sensitive_values(value: object, secrets: tuple[str, ...]) -> object:
    if not secrets:
        return value
    if isinstance(value, str):
        redacted = value
        for secret in sorted(secrets, key=len, reverse=True):
            redacted = redacted.replace(secret, REDACTION_MARKER)
        return redacted
    if isinstance(value, list):
        return [_redact_sensitive_values(item, secrets) for item in value]
    if isinstance(value, dict):
        return {key: _redact_sensitive_values(item, secrets) for key, item in value.items()}
    return value


def _validate_sensitive_values(values: tuple[str, ...]) -> tuple[str, ...]:
    if len(values) > 64 or len(values) != len(set(values)):
        raise ValueError("sensitive values must be unique and limited to 64 entries")
    if any(not value or len(value.encode("utf-8")) > 4_096 for value in values):
        raise ValueError("sensitive values must contain 1 to 4096 UTF-8 bytes")
    return values


def _duration_ms(started_at: datetime, completed_at: datetime) -> int:
    delta = completed_at - started_at
    milliseconds = delta // (datetime.resolution * 1_000)
    if completed_at < started_at or delta != datetime.resolution * 1_000 * milliseconds:
        raise ValueError("fake/parser timestamps must have a non-negative whole-millisecond delta")
    return milliseconds
