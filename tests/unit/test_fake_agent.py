"""Deterministic fake-agent behavior and replay tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

import pytest

from revanent.agents.base import agent_request_digest, canonical_json_bytes
from revanent.agents.fake import (
    FakeAgentAdapter,
    FakeAgentScenario,
    FakeAgentStep,
    ScriptedRawOutputOutcome,
    ScriptedResponseOutcome,
)
from revanent.domain import (
    BudgetLimits,
    Run,
    RunId,
    RunState,
    TaskId,
    TaskSpecification,
    WorkPackage,
    WorkPackageId,
    WorkPackageStatus,
)
from revanent.ports.agents import (
    AgentArtifactKind,
    AgentArtifactReference,
    AgentArtifactStatus,
    AgentAvailability,
    AgentCapabilities,
    AgentFailure,
    AgentFailureCategory,
    AgentRequest,
    AgentRole,
    AgentStatus,
    RepositoryPath,
    RetryDisposition,
    ScenarioId,
    SideEffectState,
    StructuredParseStatus,
)
from tests.agent_factories import (
    NOW,
    make_capabilities,
    make_payload,
    make_request,
    make_response,
)


@dataclass
class SequencedCancellation:
    cancel_on_call: int
    calls: int = 0

    def is_cancelled(self) -> bool:
        self.calls += 1
        return self.calls >= self.cancel_on_call


def success_outcome(role: AgentRole) -> ScriptedResponseOutcome:
    return ScriptedResponseOutcome(
        status=AgentStatus.COMPLETED,
        summary=f"{role.value} completed",
        structured_parse_status=StructuredParseStatus.PARSED,
        payload=make_payload(role),
    )


def failure_outcome(status: AgentStatus) -> ScriptedResponseOutcome:
    categories = {
        AgentStatus.BLOCKED: AgentFailureCategory.EXTERNAL_BLOCKER,
        AgentStatus.FAILED: AgentFailureCategory.PROVIDER_FAILURE,
        AgentStatus.TIMED_OUT: AgentFailureCategory.TIMEOUT,
        AgentStatus.CANCELLED: AgentFailureCategory.CANCELLATION,
    }
    return ScriptedResponseOutcome(
        status=status,
        summary=f"scripted {status.value.lower()}",
        structured_parse_status=StructuredParseStatus.NOT_PROVIDED,
        failure=AgentFailure(
            category=categories[status],
            code=f"scripted_{status.value.lower()}",
            message=f"scripted {status.value.lower()}",
            retry=RetryDisposition.UNKNOWN,
            side_effects=SideEffectState.POSSIBLE,
        ),
    )


def scenario_for(
    request: AgentRequest,
    *outcomes: ScriptedResponseOutcome | ScriptedRawOutputOutcome,
    capabilities: AgentCapabilities | None = None,
    durations: tuple[int, ...] | None = None,
    cancellation_checkpoints: int = 0,
) -> FakeAgentScenario:
    selected_durations = durations or tuple(1_000 for _ in outcomes)
    return FakeAgentScenario(
        scenario_id=ScenarioId("fixture.scenario"),
        capabilities=capabilities or make_capabilities(),
        default_timestamp=NOW,
        steps=tuple(
            FakeAgentStep(
                expected_request_sha256=agent_request_digest(request),
                started_at=NOW + timedelta(seconds=index),
                duration_ms=selected_durations[index],
                cancellation_checkpoints=cancellation_checkpoints,
                outcome=outcome,
            )
            for index, outcome in enumerate(outcomes)
        ),
    )


@pytest.mark.parametrize("role", list(AgentRole))
def test_successful_role_scenarios(role: AgentRole) -> None:
    request = make_request(role)
    adapter = FakeAgentAdapter(scenario_for(request, success_outcome(role)))
    result = adapter.invoke(request)
    assert result.status is AgentStatus.COMPLETED
    assert result.role is role
    assert result.payload is not None and result.payload.role is role
    assert adapter.consumed_steps == 1


@pytest.mark.parametrize("status", [AgentStatus.BLOCKED, AgentStatus.FAILED])
def test_scripted_blocked_and_failure_outcomes(status: AgentStatus) -> None:
    request = make_request()
    result = FakeAgentAdapter(scenario_for(request, failure_outcome(status))).invoke(request)
    assert result.status is status
    assert result.failure is not None


def test_unavailable_adapter_fails_before_scenario_consumption() -> None:
    request = make_request()
    capabilities = make_capabilities(
        available=AgentAvailability.UNAVAILABLE, reason="fixture provider is absent"
    )
    adapter = FakeAgentAdapter(
        scenario_for(request, success_outcome(request.role), capabilities=capabilities)
    )
    result = adapter.invoke(request)
    assert result.status is AgentStatus.UNAVAILABLE
    assert result.failure is not None
    assert result.failure.category is AgentFailureCategory.ADAPTER_UNAVAILABLE
    assert adapter.consumed_steps == 0


def test_deterministic_timeout_consumes_the_step_without_waiting() -> None:
    request = make_request(timeout_seconds=1)
    adapter = FakeAgentAdapter(
        scenario_for(request, success_outcome(request.role), durations=(1_001,))
    )
    result = adapter.invoke(request)
    assert result.status is AgentStatus.TIMED_OUT
    assert result.duration_ms == 1_001
    assert adapter.consumed_steps == 1


def test_pre_cancelled_request_does_not_consume_step() -> None:
    request = make_request(cancellation=True)
    adapter = FakeAgentAdapter(scenario_for(request, success_outcome(request.role)))
    result = adapter.invoke(request, cancellation=SequencedCancellation(cancel_on_call=1))
    assert result.status is AgentStatus.CANCELLED
    assert result.failure is not None and result.failure.side_effects is SideEffectState.NONE
    assert adapter.consumed_steps == 0


def test_mid_invocation_cancellation_is_controlled_and_ambiguous() -> None:
    request = make_request(cancellation=True)
    adapter = FakeAgentAdapter(
        scenario_for(
            request,
            success_outcome(request.role),
            cancellation_checkpoints=2,
        )
    )
    result = adapter.invoke(request, cancellation=SequencedCancellation(cancel_on_call=2))
    assert result.status is AgentStatus.CANCELLED
    assert result.failure is not None
    assert result.failure.side_effects is SideEffectState.POSSIBLE
    assert adapter.consumed_steps == 1


def test_unsupported_capability_and_role_do_not_consume_scenario() -> None:
    request = make_request(cancellation=True)
    capabilities = make_capabilities(cancellation=False)
    adapter = FakeAgentAdapter(
        scenario_for(request, success_outcome(request.role), capabilities=capabilities)
    )
    result = adapter.invoke(request)
    assert result.status is AgentStatus.FAILED
    assert result.failure is not None
    assert result.failure.category is AgentFailureCategory.UNSUPPORTED_CAPABILITY
    assert adapter.consumed_steps == 0

    reviewer = make_request(AgentRole.REVIEWER)
    builder_only = make_capabilities(roles=(AgentRole.BUILDER,))
    adapter = FakeAgentAdapter(
        scenario_for(reviewer, success_outcome(reviewer.role), capabilities=builder_only)
    )
    result = adapter.invoke(reviewer)
    assert result.failure is not None
    assert result.failure.category is AgentFailureCategory.UNSUPPORTED_CAPABILITY
    assert adapter.consumed_steps == 0


def test_exact_request_mismatch_does_not_consume_scenario() -> None:
    expected = make_request()
    actual = make_request(invocation_hex="4" * 32)
    adapter = FakeAgentAdapter(scenario_for(expected, success_outcome(expected.role)))
    result = adapter.invoke(actual)
    assert result.status is AgentStatus.FAILED
    assert result.failure is not None and result.failure.code == "scenario_request_mismatch"
    assert adapter.consumed_steps == 0


def test_ordered_responses_and_exhaustion_are_explicit() -> None:
    request = make_request()
    adapter = FakeAgentAdapter(
        scenario_for(request, failure_outcome(AgentStatus.BLOCKED), success_outcome(request.role))
    )
    assert adapter.invoke(request).status is AgentStatus.BLOCKED
    assert adapter.invoke(request).status is AgentStatus.COMPLETED
    exhausted = adapter.invoke(request)
    assert exhausted.status is AgentStatus.FAILED
    assert exhausted.failure is not None and exhausted.failure.code == "scenario_exhausted"
    assert adapter.consumed_steps == 2


def test_independent_instances_and_replay_are_identical() -> None:
    request = make_request()
    scenario = scenario_for(request, success_outcome(request.role))
    first = FakeAgentAdapter(scenario)
    second = FakeAgentAdapter(scenario)
    first_result = first.invoke(request)
    second_result = second.invoke(request)
    assert canonical_json_bytes(first_result) == canonical_json_bytes(second_result)
    assert first.consumed_steps == second.consumed_steps == 1


def test_concurrent_invocation_is_serialized_without_state_leakage() -> None:
    request = make_request()
    adapter = FakeAgentAdapter(
        scenario_for(request, success_outcome(request.role), success_outcome(request.role))
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _: adapter.invoke(request), range(2)))
    assert all(result.status is AgentStatus.COMPLETED for result in results)
    assert adapter.consumed_steps == 2


@pytest.mark.parametrize(
    ("mutator", "category"),
    [
        (lambda data: data.update(schema_version=2), AgentFailureCategory.SCHEMA_MISMATCH),
        (
            lambda data: data.update(invocation_id=f"inv_{'9' * 32}"),
            AgentFailureCategory.CORRELATION_MISMATCH,
        ),
    ],
)
def test_raw_schema_and_correlation_failures_are_normalized(
    mutator: Callable[[dict[str, object]], None], category: AgentFailureCategory
) -> None:
    request = make_request()
    data = json.loads(canonical_json_bytes(make_response()))
    mutator(data)
    raw = json.dumps(data, separators=(",", ":")).encode()
    outcome = ScriptedRawOutputOutcome(raw_output=raw)
    result = FakeAgentAdapter(scenario_for(request, outcome)).invoke(request)
    assert result.status is AgentStatus.INVALID_OUTPUT
    assert result.failure is not None and result.failure.category is category


def test_malformed_raw_output_and_artifact_reference_scenario() -> None:
    request = make_request(artifacts=True)
    malformed = ScriptedRawOutputOutcome(raw_output=b"not-json")
    result = FakeAgentAdapter(scenario_for(request, malformed)).invoke(request)
    assert result.status is AgentStatus.INVALID_OUTPUT

    artifact = AgentArtifactReference(
        root_id="run-artifacts.fixture",
        relative_path=RepositoryPath("agent/evidence.json"),
        kind=AgentArtifactKind.IMPLEMENTATION,
        content_type="application/json",
        status=AgentArtifactStatus.COMPLETE,
        observed_bytes=2,
        stored_bytes=2,
        redacted=True,
    )
    outcome = ScriptedResponseOutcome(
        status=AgentStatus.COMPLETED,
        summary="builder completed with an artifact",
        structured_parse_status=StructuredParseStatus.PARSED,
        payload=make_payload(request.role),
        artifacts=(artifact,),
    )
    completed = FakeAgentAdapter(scenario_for(request, outcome)).invoke(request)
    assert completed.status is AgentStatus.COMPLETED
    assert completed.artifacts == (artifact,)


def test_fake_provider_claims_do_not_mutate_run_or_create_approval_evidence() -> None:
    request = make_request(AgentRole.REVIEWER)
    run = Run(
        id=RunId(f"run_{'7' * 32}"),
        task=TaskSpecification(
            id=TaskId(f"task_{'8' * 32}"),
            objective="Verify adapter authority boundaries.",
            allowed_paths=("src/**",),
            acceptance_criteria=("The run remains immutable.",),
        ),
        work_package=WorkPackage(
            id=WorkPackageId("P3-001"),
            title="Agent contracts",
            objective="Keep workflow authority outside adapters.",
            status=WorkPackageStatus.IN_PROGRESS,
        ),
        budgets=BudgetLimits(
            max_duration_seconds=60,
            max_build_attempts=1,
            max_review_attempts=1,
            max_repair_attempts=0,
            max_estimated_cost_usd=Decimal("1.00"),
        ),
        state=RunState.REVIEWING,
        created_at=NOW,
        updated_at=NOW,
    )
    before = repr(run)
    result = FakeAgentAdapter(scenario_for(request, success_outcome(request.role))).invoke(request)
    assert result.status is AgentStatus.COMPLETED
    assert repr(run) == before
    assert "approval_gate" not in type(result).model_fields
