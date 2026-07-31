"""Version-1 agent schema and public-port contract tests."""

from __future__ import annotations

import json
from datetime import timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from revanent.agents.base import canonical_json_bytes
from revanent.domain import AgentAttemptId, AgentInvocationId
from revanent.ports.agents import (
    AdapterId,
    AgentArtifactKind,
    AgentArtifactReference,
    AgentArtifactStatus,
    AgentAvailability,
    AgentCapabilities,
    AgentDiagnostic,
    AgentDiagnosticSeverity,
    AgentFailure,
    AgentFailureCategory,
    AgentRequest,
    AgentResponse,
    AgentRole,
    AgentStatus,
    AgentUsage,
    AgentUsageSource,
    ContextReference,
    ExpectedAgentCapabilities,
    ProviderId,
    RepositoryPath,
    RetryDisposition,
    ScopePath,
    SideEffectState,
    StructuredParseStatus,
    WorkspaceKind,
    WorkspaceReference,
)
from tests.agent_factories import NOW, make_capabilities, make_request, make_response


@pytest.mark.parametrize(
    ("model", "model_type"),
    [
        (make_request(), AgentRequest),
        (make_response(), AgentResponse),
        (make_capabilities(), AgentCapabilities),
    ],
)
def test_version_one_contracts_round_trip(model: object, model_type: type[object]) -> None:
    serialized = model.model_dump_json()  # type: ignore[attr-defined]
    assert model_type.model_validate_json(serialized) == model  # type: ignore[attr-defined]


def test_canonical_serialization_is_stable_and_sorted() -> None:
    request = make_request()
    first = canonical_json_bytes(request)
    second = canonical_json_bytes(AgentRequest.model_validate_json(request.model_dump_json()))
    assert first == second
    assert (
        first
        == json.dumps(
            json.loads(first),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )


@pytest.mark.parametrize("model", [make_request(), make_response(), make_capabilities()])
def test_contract_models_are_immutable(model: object) -> None:
    with pytest.raises(ValidationError):
        model.schema_version = 2  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("model_type", "valid"),
    [
        (AgentRequest, make_request()),
        (AgentResponse, make_response()),
        (AgentCapabilities, make_capabilities()),
    ],
)
def test_unknown_fields_and_schema_versions_are_rejected(
    model_type: type[object], valid: object
) -> None:
    data = valid.model_dump(mode="json")  # type: ignore[attr-defined]
    data["unexpected"] = True
    with pytest.raises(ValidationError):
        model_type.model_validate(data)  # type: ignore[attr-defined]
    data.pop("unexpected")
    data["schema_version"] = 2
    with pytest.raises(ValidationError):
        model_type.model_validate(data)  # type: ignore[attr-defined]


def test_missing_required_request_field_is_rejected() -> None:
    data = make_request().model_dump(mode="json")
    del data["invocation_id"]
    with pytest.raises(ValidationError):
        AgentRequest.model_validate(data)


@pytest.mark.parametrize(
    ("identifier", "model_type"),
    [
        (f"inv_{'a' * 32}", AgentInvocationId),
        (f"attempt_{'b' * 32}", AgentAttemptId),
        ("fake.provider", ProviderId),
        ("fake.adapter", AdapterId),
    ],
)
def test_agent_identifiers_round_trip(identifier: str, model_type: type[object]) -> None:
    parsed = model_type(identifier)  # type: ignore[call-arg]
    assert model_type.model_validate_json(parsed.model_dump_json()) == parsed  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "identifier",
    ["INV_" + "a" * 32, "inv_short", "inv_" + "A" * 32, "inv_" + "a" * 31 + "/"],
)
def test_invocation_identifier_rejects_unsafe_spelling(identifier: str) -> None:
    with pytest.raises(ValueError):
        AgentInvocationId(identifier)


def test_agent_response_requires_utc_and_ordered_timestamps() -> None:
    data = make_response().model_dump(mode="python")
    data["started_at"] = NOW.replace(tzinfo=None)
    with pytest.raises(ValidationError, match="UTC"):
        AgentResponse.model_validate(data)

    data = make_response().model_dump(mode="python")
    data["started_at"] = NOW.astimezone(timezone(timedelta(hours=1)))
    with pytest.raises(ValidationError, match="UTC"):
        AgentResponse.model_validate(data)

    data = make_response().model_dump(mode="python")
    data["completed_at"] = NOW - timedelta(milliseconds=1)
    data["duration_ms"] = 0
    with pytest.raises(ValidationError, match="precede"):
        AgentResponse.model_validate(data)


def test_request_bounds_and_duplicate_ordering_are_strict() -> None:
    data = make_request().model_dump(mode="python")
    data["objective"] = "x" * 8_193
    with pytest.raises(ValidationError):
        AgentRequest.model_validate(data)

    data = make_request().model_dump(mode="python")
    data["allowed_scope"] = (ScopePath("src/**"), ScopePath("src/**"))
    with pytest.raises(ValidationError, match="unique"):
        AgentRequest.model_validate(data)

    data = make_request().model_dump(mode="python")
    data["allowed_environment_names"] = ("PATH", "LANG")
    with pytest.raises(ValidationError, match="sorted"):
        AgentRequest.model_validate(data)


def test_paths_reject_absolute_traversal_and_ambiguous_separators() -> None:
    for value in ("../secret", "/absolute", "C:/absolute", "src\\file.py", "src/./file.py"):
        with pytest.raises(ValueError):
            RepositoryPath(value)
    with pytest.raises(ValueError):
        RepositoryPath("src/*.py")
    assert ScopePath("src/**/*.py").root == "src/**/*.py"


def test_workspace_requires_an_absolute_path() -> None:
    with pytest.raises(ValidationError):
        WorkspaceReference(
            kind=WorkspaceKind.WORKTREE,
            reference_id="fixture",
            root=Path("relative"),
        )


def test_reviewer_and_repairer_role_invariants() -> None:
    reviewer = make_request(AgentRole.REVIEWER).model_dump(mode="python")
    reviewer["expected_capabilities"] = ExpectedAgentCapabilities(requires_repository_writes=True)
    with pytest.raises(ValidationError, match="reviewer"):
        AgentRequest.model_validate(reviewer)

    repairer = make_request(AgentRole.REPAIRER).model_dump(mode="python")
    repairer["expected_capabilities"] = ExpectedAgentCapabilities(
        requires_repository_writes=True,
        requires_repair=False,
    )
    with pytest.raises(ValidationError, match="repair"):
        AgentRequest.model_validate(repairer)


def test_prior_findings_are_repair_only() -> None:
    data = make_request().model_dump(mode="python")
    artifact = AgentArtifactReference(
        root_id="run-artifacts.fixture",
        relative_path=RepositoryPath("findings/one.json"),
        kind=AgentArtifactKind.REVIEW,
        content_type="application/json",
        status=AgentArtifactStatus.COMPLETE,
        observed_bytes=2,
        stored_bytes=2,
        redacted=True,
    )
    data["prior_findings"] = ({"reference_id": "finding.one", "artifact": artifact},)
    with pytest.raises(ValidationError, match="repairer"):
        AgentRequest.model_validate(data)


def test_status_failure_and_parse_consistency() -> None:
    data = make_response().model_dump(mode="python")
    data["status"] = AgentStatus.FAILED
    with pytest.raises(ValidationError, match="non-completed"):
        AgentResponse.model_validate(data)

    data = make_response().model_dump(mode="python")
    data["payload"] = None
    data["status"] = AgentStatus.TIMED_OUT
    data["structured_parse_status"] = StructuredParseStatus.NOT_PROVIDED
    data["failure"] = AgentFailure(
        category=AgentFailureCategory.PROVIDER_FAILURE,
        code="wrong_category",
        message="wrong category",
        retry=RetryDisposition.UNKNOWN,
        side_effects=SideEffectState.POSSIBLE,
    )
    with pytest.raises(ValidationError, match="agree"):
        AgentResponse.model_validate(data)


def test_capability_contract_requires_explicit_consistent_facts() -> None:
    data = make_capabilities().model_dump(mode="python")
    data["supported_roles"] = (AgentRole.REVIEWER, AgentRole.BUILDER)
    with pytest.raises(ValidationError, match="sorted"):
        AgentCapabilities.model_validate(data)

    data = make_capabilities().model_dump(mode="python")
    data["availability"] = AgentAvailability.UNAVAILABLE
    with pytest.raises(ValidationError, match="reason"):
        AgentCapabilities.model_validate(data)


def test_artifact_references_are_relative_bounded_and_redacted() -> None:
    valid = AgentArtifactReference(
        root_id="run-artifacts.fixture",
        relative_path=RepositoryPath("agent/raw.json"),
        kind=AgentArtifactKind.RAW_OUTPUT,
        content_type="application/json",
        status=AgentArtifactStatus.TRUNCATED,
        observed_bytes=10,
        stored_bytes=5,
        redacted=True,
    )
    assert valid.schema_version == 1
    data = valid.model_dump(mode="python")
    data["redacted"] = False
    with pytest.raises(ValidationError, match="redacted"):
        AgentArtifactReference.model_validate(data)
    data = valid.model_dump(mode="python")
    data["relative_path"] = "../escape"
    with pytest.raises(ValidationError):
        AgentArtifactReference.model_validate(data)


def test_duplicate_context_and_response_artifacts_are_rejected() -> None:
    artifact = AgentArtifactReference(
        root_id="run-artifacts.fixture",
        relative_path=RepositoryPath("context/one.json"),
        kind=AgentArtifactKind.CONTEXT,
        content_type="application/json",
        status=AgentArtifactStatus.COMPLETE,
        observed_bytes=2,
        stored_bytes=2,
        redacted=True,
    )
    context = ContextReference(reference_id="context.one", purpose="Fixture", artifact=artifact)
    request = make_request().model_dump(mode="python")
    request["context"] = (context, context)
    with pytest.raises(ValidationError, match="unique"):
        AgentRequest.model_validate(request)

    response = make_response().model_dump(mode="python")
    response["artifacts"] = (artifact, artifact)
    with pytest.raises(ValidationError, match="unique"):
        AgentResponse.model_validate(response)


def test_usage_is_explicitly_labeled_reported() -> None:
    usage = AgentUsage(input_tokens=2, output_tokens=3, total_tokens=5)
    assert usage.source is AgentUsageSource.REPORTED


def test_retryable_failure_cannot_hide_ambiguous_side_effects() -> None:
    with pytest.raises(ValidationError, match="no side effect"):
        AgentFailure(
            category=AgentFailureCategory.PROVIDER_FAILURE,
            code="ambiguous_retry",
            message="Side effects are unknown.",
            retry=RetryDisposition.RETRYABLE,
            side_effects=SideEffectState.POSSIBLE,
        )


def test_response_collections_have_contract_level_hard_bounds() -> None:
    response = make_response().model_dump(mode="python")
    response["diagnostics"] = tuple(
        AgentDiagnostic(
            severity=AgentDiagnosticSeverity.INFO,
            code=f"diagnostic.{index:02d}",
            message="Bounded diagnostic.",
        )
        for index in range(65)
    )
    with pytest.raises(ValidationError, match="hard collection limit"):
        AgentResponse.model_validate(response)
