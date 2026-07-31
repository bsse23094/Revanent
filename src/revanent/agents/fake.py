"""Finite, declarative, deterministic fake implementation of the agent port."""

from __future__ import annotations

from datetime import datetime, timedelta
from threading import Lock
from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from revanent.agents.base import (
    agent_request_digest,
    invalid_output_response,
    normalize_agent_output,
    request_compatibility_failure,
    validate_agent_response_semantics,
)
from revanent.ports.agents import (
    AGENT_SCHEMA_VERSION,
    MAX_AGENT_COLLECTION_ITEMS,
    AgentArtifactReference,
    AgentCapabilities,
    AgentDiagnostic,
    AgentFailure,
    AgentFailureCategory,
    AgentOutputLimits,
    AgentPayload,
    AgentProviderIdentity,
    AgentRequest,
    AgentResponse,
    AgentStatus,
    AgentUsage,
    RetryDisposition,
    ScenarioId,
    SideEffectState,
    StructuredParseStatus,
    _AgentModel,
    _require_utc,
)
from revanent.ports.commands import CancellationToken


class ScriptedResponseOutcome(_AgentModel):
    """A normalized provider outcome generated with request correlation."""

    outcome_type: Literal["RESPONSE"] = "RESPONSE"
    status: AgentStatus
    summary: Annotated[str, Field(min_length=1, max_length=2_048)]
    public_text: Annotated[str, Field(max_length=65_536)] = ""
    structured_parse_status: StructuredParseStatus
    payload: AgentPayload | None = None
    diagnostics: tuple[AgentDiagnostic, ...] = ()
    artifacts: tuple[AgentArtifactReference, ...] = ()
    usage: AgentUsage | None = None
    failure: AgentFailure | None = None
    raw_output_artifact: AgentArtifactReference | None = None

    @model_validator(mode="after")
    def _validate_shape(self) -> Self:
        if len(self.public_text.encode("utf-8")) > 65_536:
            raise ValueError("scripted public text exceeds the hard byte limit")
        if len(self.diagnostics) > 64 or len(self.artifacts) > 64:
            raise ValueError("scripted diagnostics and artifacts are limited to 64 entries")
        if self.status is AgentStatus.COMPLETED:
            if self.payload is None or self.failure is not None:
                raise ValueError("completed scripted outcomes require payload and no failure")
        elif self.payload is not None or self.failure is None:
            raise ValueError("non-completed scripted outcomes require failure and no payload")
        return self


class ScriptedRawOutputOutcome(_AgentModel):
    """Untrusted bytes routed through the same strict parser used by live adapters."""

    outcome_type: Literal["RAW_OUTPUT"] = "RAW_OUTPUT"
    raw_output: bytes

    @field_validator("raw_output")
    @classmethod
    def _bound_raw_output(cls, value: bytes) -> bytes:
        if len(value) > 1 * 1_024 * 1_024:
            raise ValueError("scripted raw output is limited to the parser hard ceiling")
        return value


FakeOutcome = Annotated[
    ScriptedResponseOutcome | ScriptedRawOutputOutcome,
    Field(discriminator="outcome_type"),
]


class FakeAgentStep(_AgentModel):
    """One finite fake invocation with an exact request signature."""

    expected_request_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    started_at: datetime
    duration_ms: int = Field(ge=0, le=86_400_000)
    cancellation_checkpoints: int = Field(default=0, ge=0, le=1_000)
    outcome: FakeOutcome

    _started_at_utc = field_validator("started_at")(_require_utc)


class FakeAgentScenario(_AgentModel):
    """Immutable script definition; adapter instances own isolated consumption state."""

    schema_version: Literal[1] = AGENT_SCHEMA_VERSION
    scenario_id: ScenarioId
    capabilities: AgentCapabilities
    default_timestamp: datetime
    steps: tuple[FakeAgentStep, ...] = ()
    output_limits: AgentOutputLimits = Field(default_factory=AgentOutputLimits)

    _default_timestamp_utc = field_validator("default_timestamp")(_require_utc)

    @model_validator(mode="after")
    def _bound_steps(self) -> Self:
        if len(self.steps) > MAX_AGENT_COLLECTION_ITEMS:
            raise ValueError(f"fake scenarios are limited to {MAX_AGENT_COLLECTION_ITEMS} steps")
        return self


class FakeAgentAdapter:
    """Deterministic in-memory adapter implementing the public ``AgentAdapter`` port."""

    def __init__(self, scenario: FakeAgentScenario) -> None:
        if not isinstance(scenario, FakeAgentScenario):
            raise TypeError("scenario must be a validated FakeAgentScenario")
        self._scenario = scenario
        self._position = 0
        self._invocation_count = 0
        self._lock = Lock()

    @property
    def capabilities(self) -> AgentCapabilities:
        return self._scenario.capabilities

    @property
    def invocation_count(self) -> int:
        with self._lock:
            return self._invocation_count

    @property
    def consumed_steps(self) -> int:
        with self._lock:
            return self._position

    def invoke(
        self, request: AgentRequest, *, cancellation: CancellationToken | None = None
    ) -> AgentResponse:
        """Consume at most one exactly matching step and return only typed outcomes."""
        if not isinstance(request, AgentRequest):
            raise TypeError("request must be a validated AgentRequest")
        with self._lock:
            self._invocation_count += 1
            if cancellation is not None and cancellation.is_cancelled():
                return self._failure_response(
                    request,
                    status=AgentStatus.CANCELLED,
                    failure=AgentFailure(
                        category=AgentFailureCategory.CANCELLATION,
                        code="cancelled_before_invocation",
                        message="agent invocation was cancelled before scripted execution",
                        retry=RetryDisposition.RETRYABLE,
                        side_effects=SideEffectState.NONE,
                    ),
                )

            compatibility = request_compatibility_failure(self.capabilities, request)
            if compatibility is not None:
                status = (
                    AgentStatus.UNAVAILABLE
                    if compatibility.category is AgentFailureCategory.ADAPTER_UNAVAILABLE
                    else AgentStatus.FAILED
                )
                return self._failure_response(request, status=status, failure=compatibility)

            if self._position >= len(self._scenario.steps):
                return self._failure_response(
                    request,
                    status=AgentStatus.FAILED,
                    failure=AgentFailure(
                        category=AgentFailureCategory.INVOCATION_FAILURE,
                        code="scenario_exhausted",
                        message="deterministic fake scenario has no remaining scripted step",
                        retry=RetryDisposition.NOT_RETRYABLE,
                        side_effects=SideEffectState.NONE,
                    ),
                )

            step = self._scenario.steps[self._position]
            if agent_request_digest(request) != step.expected_request_sha256:
                return self._failure_response(
                    request,
                    status=AgentStatus.FAILED,
                    failure=AgentFailure(
                        category=AgentFailureCategory.INVALID_REQUEST,
                        code="scenario_request_mismatch",
                        message="request did not match the next scripted fake expectation",
                        retry=RetryDisposition.NOT_RETRYABLE,
                        side_effects=SideEffectState.NONE,
                    ),
                    timestamp=step.started_at,
                )

            self._position += 1
            completed_at = step.started_at + timedelta(milliseconds=step.duration_ms)
            for _ in range(step.cancellation_checkpoints):
                if cancellation is not None and cancellation.is_cancelled():
                    return self._failure_response(
                        request,
                        status=AgentStatus.CANCELLED,
                        failure=AgentFailure(
                            category=AgentFailureCategory.CANCELLATION,
                            code="cancelled_during_invocation",
                            message="agent invocation was cancelled at a scripted boundary",
                            retry=RetryDisposition.UNKNOWN,
                            side_effects=SideEffectState.POSSIBLE,
                        ),
                        timestamp=step.started_at,
                        completed_at=completed_at,
                    )
            if step.duration_ms > request.timeout_seconds * 1_000:
                return self._failure_response(
                    request,
                    status=AgentStatus.TIMED_OUT,
                    failure=AgentFailure(
                        category=AgentFailureCategory.TIMEOUT,
                        code="scripted_timeout",
                        message="agent invocation exceeded its deterministic timeout",
                        retry=RetryDisposition.UNKNOWN,
                        side_effects=SideEffectState.POSSIBLE,
                    ),
                    timestamp=step.started_at,
                    completed_at=completed_at,
                )

            try:
                return self._execute_step(request, step, completed_at=completed_at)
            except Exception:
                return self._failure_response(
                    request,
                    status=AgentStatus.FAILED,
                    failure=AgentFailure(
                        category=AgentFailureCategory.INTERNAL_ADAPTER_FAILURE,
                        code="fake_adapter_internal",
                        message="fake adapter could not normalize the scripted outcome",
                        retry=RetryDisposition.UNKNOWN,
                        side_effects=SideEffectState.POSSIBLE,
                    ),
                    timestamp=step.started_at,
                    completed_at=completed_at,
                )

    def _execute_step(
        self, request: AgentRequest, step: FakeAgentStep, *, completed_at: datetime
    ) -> AgentResponse:
        identity = self._identity()
        if isinstance(step.outcome, ScriptedRawOutputOutcome):
            response = normalize_agent_output(
                step.outcome.raw_output,
                request,
                identity=identity,
                started_at=step.started_at,
                completed_at=completed_at,
                limits=self._scenario.output_limits,
            )
            if response.status is not AgentStatus.INVALID_OUTPUT and response.identity != identity:
                return invalid_output_response(
                    request,
                    identity=identity,
                    started_at=step.started_at,
                    completed_at=completed_at,
                    category=AgentFailureCategory.CORRELATION_MISMATCH,
                    code="adapter_identity_mismatch",
                    message="agent response identity did not match the selected adapter",
                )
            return response

        outcome = step.outcome
        response = AgentResponse(
            invocation_id=request.invocation_id,
            run_id=request.run_id,
            work_package_id=request.work_package_id,
            attempt_id=request.attempt_id,
            attempt_number=request.attempt_number,
            role=request.role,
            expected_response_schema_version=request.response_contract.schema_version,
            status=outcome.status,
            started_at=step.started_at,
            completed_at=completed_at,
            duration_ms=step.duration_ms,
            summary=outcome.summary,
            public_text=outcome.public_text,
            structured_parse_status=outcome.structured_parse_status,
            payload=outcome.payload,
            diagnostics=outcome.diagnostics,
            artifacts=outcome.artifacts,
            usage=outcome.usage,
            identity=identity,
            failure=outcome.failure,
            raw_output_artifact=outcome.raw_output_artifact,
        )
        validate_agent_response_semantics(response, request)
        return response

    def _failure_response(
        self,
        request: AgentRequest,
        *,
        status: AgentStatus,
        failure: AgentFailure,
        timestamp: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> AgentResponse:
        started_at = timestamp or self._scenario.default_timestamp
        finished_at = completed_at or started_at
        duration_ms = (finished_at - started_at) // timedelta(milliseconds=1)
        return AgentResponse(
            invocation_id=request.invocation_id,
            run_id=request.run_id,
            work_package_id=request.work_package_id,
            attempt_id=request.attempt_id,
            attempt_number=request.attempt_number,
            role=request.role,
            expected_response_schema_version=request.response_contract.schema_version,
            status=status,
            started_at=started_at,
            completed_at=finished_at,
            duration_ms=duration_ms,
            summary=failure.message,
            structured_parse_status=StructuredParseStatus.NOT_PROVIDED,
            identity=self._identity(),
            failure=failure,
        )

    def _identity(self) -> AgentProviderIdentity:
        capabilities = self._scenario.capabilities
        return AgentProviderIdentity(
            provider_id=capabilities.provider_id,
            adapter_id=capabilities.adapter_id,
            adapter_version=capabilities.adapter_version,
            model=capabilities.detected_model,
        )
