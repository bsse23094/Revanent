"""Provider-neutral durable orchestration contracts."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator

from revanent.domain import (
    AgentAttemptId,
    AgentInvocationId,
    Run,
    RunId,
    RunState,
    WorkPackageId,
)
from revanent.ports.agents import (
    AdapterId,
    AgentRequest,
    AgentResponse,
    AgentRole,
    AgentStatus,
    SideEffectState,
)
from revanent.ports.context import ContextManifest, ContextSelectionRequest
from revanent.ports.git import (
    WorktreeCreationRequest,
    WorktreeId,
    WorktreeLifecycleStatus,
)
from revanent.ports.storage import StoredRun
from revanent.ports.validation import ValidationPlan, ValidationPlanResult, ValidationStatus
from revanent.review import LocalApprovalEvidence, ReviewGateDecision

ORCHESTRATION_SCHEMA_VERSION: Literal[1] = 1
MAX_ORCHESTRATION_RECORDS = 1_024
MAX_ORCHESTRATION_PLANS = 101

_ATTEMPT_ID = re.compile(r"^oattempt_[0-9a-f]{32}$")
_RECORD_ID = re.compile(r"^orec_[0-9a-f]{64}$")


class _OrchestrationModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=True,
    )


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("orchestration timestamps must be timezone-aware UTC")
    return value


class OrchestrationAttemptId(RootModel[str]):
    model_config = ConfigDict(frozen=True, strict=True)

    @model_validator(mode="after")
    def _validate_root(self) -> Self:
        if _ATTEMPT_ID.fullmatch(self.root) is None:
            raise ValueError("orchestration attempt ID must use oattempt_ plus 32 hex characters")
        return self

    def __str__(self) -> str:
        return self.root


class OrchestrationRecordId(RootModel[str]):
    model_config = ConfigDict(frozen=True, strict=True)

    @model_validator(mode="after")
    def _validate_root(self) -> Self:
        if _RECORD_ID.fullmatch(self.root) is None:
            raise ValueError("orchestration record ID must use orec_ plus 64 hex characters")
        return self

    def __str__(self) -> str:
        return self.root


class OrchestrationStep(StrEnum):
    CONTEXT = "CONTEXT"
    WORKSPACE = "WORKSPACE"
    BUILD = "BUILD"
    VALIDATION = "VALIDATION"
    REVIEW = "REVIEW"
    REPAIR = "REPAIR"


class OrchestrationRecordStage(StrEnum):
    INTENT = "INTENT"
    OUTCOME = "OUTCOME"
    RECONCILIATION = "RECONCILIATION"


class AttemptStatus(StrEnum):
    INTENDED = "INTENDED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"
    INVALID = "INVALID"
    AMBIGUOUS = "AMBIGUOUS"


class ReconciliationState(StrEnum):
    KNOWN_NONE = "KNOWN_NONE"
    KNOWN_PRESENT = "KNOWN_PRESENT"
    AMBIGUOUS = "AMBIGUOUS"
    INCOMPATIBLE = "INCOMPATIBLE"


class OrchestrationStatus(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    APPROVED = "APPROVED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"
    STALE = "STALE"


class LimitOutcome(StrEnum):
    NONE = "NONE"
    BUILD_ATTEMPTS_EXHAUSTED = "BUILD_ATTEMPTS_EXHAUSTED"
    REVIEW_ATTEMPTS_EXHAUSTED = "REVIEW_ATTEMPTS_EXHAUSTED"
    REPAIR_ATTEMPTS_EXHAUSTED = "REPAIR_ATTEMPTS_EXHAUSTED"
    RUN_DURATION_EXHAUSTED = "RUN_DURATION_EXHAUSTED"


class RepairStrategy(StrEnum):
    LOCAL_BUILDER = "LOCAL_BUILDER"
    CODEX_REPAIR = "CODEX_REPAIR"
    NO_REPAIR = "NO_REPAIR"
    BLOCKED = "BLOCKED"


class RepairReason(StrEnum):
    MECHANICAL_FIRST_FAILURE = "mechanical_first_failure"
    REPEATED_DEFECT = "repeated_defect"
    HIGH_RISK_DEFECT = "high_risk_defect"
    LOCAL_REPAIR_UNAVAILABLE = "local_repair_unavailable"
    CODEX_REPAIR_UNAVAILABLE = "codex_repair_unavailable"
    CODEX_REPAIR_NOT_AUTHORIZED = "codex_repair_not_authorized"
    LIMIT_EXHAUSTED = "limit_exhausted"
    CANCELLED = "cancelled"
    SIDE_EFFECTS_UNRESOLVED = "side_effects_unresolved"
    SCOPE_VIOLATION = "scope_violation"
    INVALID_EVIDENCE = "invalid_evidence"
    EXTERNAL_REQUIREMENT = "external_requirement"


class OrchestrationFailure(_OrchestrationModel):
    code: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")]
    message: Annotated[str, Field(min_length=1, max_length=1_024)]


class WorkspaceEvidence(_OrchestrationModel):
    worktree_id: WorktreeId
    lifecycle: WorktreeLifecycleStatus
    path: Annotated[str, Field(min_length=1, max_length=4_096)]
    branch: Annotated[str, Field(min_length=1, max_length=255)]
    repository_id: Annotated[str, Field(pattern=r"^repo_[0-9a-f]{64}$")]


class RepairPolicyInput(_OrchestrationModel):
    defect_fingerprints: tuple[
        Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_.:-]{0,127}$")], ...
    ] = Field(default=(), max_length=64)
    repeated_defect_count: int = Field(default=0, ge=0, le=100)
    high_risk: bool = False
    malformed_builder_repeated: bool = False
    local_builder_available: bool
    codex_repair_available: bool
    codex_repair_authorized: bool = False
    repairs_remaining: int = Field(ge=0, le=100)
    cancelled: bool = False
    side_effects_reconciled: bool = True
    scope_valid: bool = True
    evidence_valid: bool = True
    external_requirement: bool = False

    @model_validator(mode="after")
    def _validate_fingerprints(self) -> Self:
        if tuple(sorted(set(self.defect_fingerprints))) != self.defect_fingerprints:
            raise ValueError("defect fingerprints must be sorted and unique")
        return self


class RepairDecision(_OrchestrationModel):
    schema_version: Literal[1] = ORCHESTRATION_SCHEMA_VERSION
    strategy: RepairStrategy
    reasons: tuple[RepairReason, ...]
    defect_fingerprints: tuple[str, ...] = Field(default=(), max_length=64)
    repair_sequence: int = Field(ge=1, le=100)

    @model_validator(mode="after")
    def _validate_decision(self) -> Self:
        if not self.reasons or len(self.reasons) != len(set(self.reasons)):
            raise ValueError("repair decisions require unique deterministic reasons")
        if tuple(sorted(set(self.defect_fingerprints))) != self.defect_fingerprints:
            raise ValueError("repair-decision fingerprints must be sorted and unique")
        return self


class _AttemptBase(_OrchestrationModel):
    schema_version: Literal[1] = ORCHESTRATION_SCHEMA_VERSION
    attempt_id: OrchestrationAttemptId
    run_id: RunId
    work_package_id: WorkPackageId
    sequence: int = Field(ge=1, le=100)
    status: AttemptStatus
    started_at: datetime
    completed_at: datetime | None = None
    side_effects: ReconciliationState
    failure: OrchestrationFailure | None = None

    _started_utc = field_validator("started_at")(_require_utc)
    _completed_utc = field_validator("completed_at")(
        lambda value: _require_utc(value) if value is not None else None
    )

    @model_validator(mode="after")
    def _validate_common(self) -> Self:
        if self.status is AttemptStatus.INTENDED:
            if self.completed_at is not None or self.failure is not None:
                raise ValueError("intended attempts cannot carry terminal evidence")
        else:
            if self.completed_at is None:
                raise ValueError("terminal attempts require a completion timestamp")
            if self.completed_at < self.started_at:
                raise ValueError("attempt completion cannot precede start")
            if self.status is AttemptStatus.COMPLETED and self.failure is not None:
                raise ValueError("completed attempts cannot carry failures")
            if self.status is not AttemptStatus.COMPLETED and self.failure is None:
                raise ValueError("non-completed terminal attempts require a failure")
        return self


class WorkspaceAttempt(_AttemptBase):
    kind: Literal[OrchestrationStep.WORKSPACE] = OrchestrationStep.WORKSPACE
    request: WorktreeCreationRequest
    evidence: WorkspaceEvidence | None = None

    @model_validator(mode="after")
    def _validate_workspace(self) -> Self:
        if self.request.worktree_id.root != (
            self.evidence.worktree_id.root
            if self.evidence is not None
            else self.request.worktree_id.root
        ):
            raise ValueError("workspace evidence does not match its request")
        if self.status is AttemptStatus.COMPLETED and self.evidence is None:
            raise ValueError("completed workspace attempts require ownership evidence")
        if self.status is AttemptStatus.INTENDED and self.evidence is not None:
            raise ValueError("workspace intent cannot carry outcome evidence")
        return self


class ContextAttempt(_AttemptBase):
    kind: Literal[OrchestrationStep.CONTEXT] = OrchestrationStep.CONTEXT
    request_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
    role: AgentRole
    manifest: ContextManifest | None = None

    @model_validator(mode="after")
    def _validate_context(self) -> Self:
        if self.status is AttemptStatus.COMPLETED:
            if self.manifest is None:
                raise ValueError("completed context attempts require a manifest")
            if (
                self.manifest.run_id != self.run_id
                or self.manifest.work_package_id != self.work_package_id
                or self.manifest.role is not self.role
                or not self.manifest.required_evidence_complete
            ):
                raise ValueError("context manifest correlation or completeness mismatch")
        elif self.manifest is not None:
            raise ValueError("non-completed context attempts cannot carry a manifest")
        return self


class BuildAttempt(_AttemptBase):
    kind: Literal[OrchestrationStep.BUILD] = OrchestrationStep.BUILD
    role: Literal[AgentRole.BUILDER] = AgentRole.BUILDER
    agent_attempt_id: AgentAttemptId
    invocation_id: AgentInvocationId
    adapter_id: AdapterId
    request: AgentRequest
    response: AgentResponse | None = None

    @model_validator(mode="after")
    def _validate_agent(self) -> Self:
        _validate_agent_attempt(self, AgentRole.BUILDER)
        return self


class ValidationAttempt(_AttemptBase):
    kind: Literal[OrchestrationStep.VALIDATION] = OrchestrationStep.VALIDATION
    plan: ValidationPlan
    result: ValidationPlanResult | None = None

    @model_validator(mode="after")
    def _validate_validation(self) -> Self:
        if self.plan.run_id != self.run_id or self.plan.work_package_id != self.work_package_id:
            raise ValueError("validation attempt plan correlation mismatch")
        if self.status is AttemptStatus.COMPLETED and self.result is None:
            raise ValueError("completed validation attempt requires a result")
        if self.result is not None and (
            self.result.plan_id != self.plan.id
            or self.result.run_id != self.run_id
            or self.result.work_package_id != self.work_package_id
        ):
            raise ValueError("validation attempt result correlation mismatch")
        if self.status is AttemptStatus.INTENDED and self.result is not None:
            raise ValueError("validation intent cannot carry a result")
        return self


class ReviewAttempt(_AttemptBase):
    kind: Literal[OrchestrationStep.REVIEW] = OrchestrationStep.REVIEW
    role: Literal[AgentRole.REVIEWER] = AgentRole.REVIEWER
    agent_attempt_id: AgentAttemptId
    invocation_id: AgentInvocationId
    adapter_id: AdapterId
    request: AgentRequest
    response: AgentResponse | None = None
    validation_plan: ValidationPlan
    validation_result: ValidationPlanResult
    local_evidence: LocalApprovalEvidence | None = None
    gate_decision: ReviewGateDecision | None = None

    @model_validator(mode="after")
    def _validate_review(self) -> Self:
        _validate_agent_attempt(self, AgentRole.REVIEWER)
        if self.status is AttemptStatus.BLOCKED and (
            self.response is None and self.local_evidence is None and self.gate_decision is None
        ):
            return self
        terminal = self.status is not AttemptStatus.INTENDED
        if terminal != (
            self.response is not None
            and self.local_evidence is not None
            and self.gate_decision is not None
        ):
            raise ValueError("terminal review attempts require response, local evidence, and gate")
        return self


class RepairAttempt(_AttemptBase):
    kind: Literal[OrchestrationStep.REPAIR] = OrchestrationStep.REPAIR
    role: AgentRole
    agent_attempt_id: AgentAttemptId
    invocation_id: AgentInvocationId
    adapter_id: AdapterId
    decision: RepairDecision
    request: AgentRequest
    response: AgentResponse | None = None
    write_authorized: bool

    @model_validator(mode="after")
    def _validate_repair(self) -> Self:
        if self.role not in {AgentRole.BUILDER, AgentRole.REPAIRER}:
            raise ValueError("repair attempt role must be BUILDER or REPAIRER")
        _validate_agent_attempt(self, self.role)
        if (
            self.decision.strategy is RepairStrategy.LOCAL_BUILDER
            and self.role is not AgentRole.BUILDER
        ):
            raise ValueError("local-builder repair requires BUILDER role")
        if self.decision.strategy is RepairStrategy.CODEX_REPAIR and (
            self.role is not AgentRole.REPAIRER or not self.write_authorized
        ):
            raise ValueError("Codex repair requires REPAIRER role and explicit write authority")
        if self.status is AttemptStatus.INTENDED and self.response is not None:
            raise ValueError("repair intent cannot carry a response")
        return self


AttemptEvidence = Annotated[
    ContextAttempt
    | WorkspaceAttempt
    | BuildAttempt
    | ValidationAttempt
    | ReviewAttempt
    | RepairAttempt,
    Field(discriminator="kind"),
]


class ReconciliationResult(_OrchestrationModel):
    schema_version: Literal[1] = ORCHESTRATION_SCHEMA_VERSION
    attempt_id: OrchestrationAttemptId
    state: ReconciliationState
    safe_to_continue: bool
    reason: Annotated[str, Field(min_length=1, max_length=1_024)]
    observed_at: datetime

    _observed_utc = field_validator("observed_at")(_require_utc)

    @model_validator(mode="after")
    def _validate_safety(self) -> Self:
        if self.safe_to_continue and self.state not in {
            ReconciliationState.KNOWN_NONE,
            ReconciliationState.KNOWN_PRESENT,
        }:
            raise ValueError("ambiguous or incompatible reconciliation cannot continue")
        return self


class OrchestrationRecord(_OrchestrationModel):
    schema_version: Literal[1] = ORCHESTRATION_SCHEMA_VERSION
    id: OrchestrationRecordId
    run_id: RunId
    work_package_id: WorkPackageId
    sequence: int = Field(ge=1, le=MAX_ORCHESTRATION_RECORDS)
    run_revision: int = Field(ge=0)
    expected_state: RunState
    stage: OrchestrationRecordStage
    occurred_at: datetime
    attempt: AttemptEvidence
    reconciliation: ReconciliationResult | None = None

    _occurred_utc = field_validator("occurred_at")(_require_utc)

    @model_validator(mode="after")
    def _validate_record(self) -> Self:
        if (
            self.attempt.run_id != self.run_id
            or self.attempt.work_package_id != self.work_package_id
        ):
            raise ValueError("orchestration record correlation mismatch")
        if self.stage is OrchestrationRecordStage.INTENT:
            if self.attempt.status is not AttemptStatus.INTENDED or self.reconciliation is not None:
                raise ValueError("intent records require only intended attempt evidence")
        elif self.stage is OrchestrationRecordStage.OUTCOME:
            if self.attempt.status is AttemptStatus.INTENDED or self.reconciliation is not None:
                raise ValueError("outcome records require terminal attempt evidence")
        elif (
            self.reconciliation is None or self.reconciliation.attempt_id != self.attempt.attempt_id
        ):
            raise ValueError("reconciliation records require matching reconciliation evidence")
        return self


class OrchestrationRequest(_OrchestrationModel):
    schema_version: Literal[1] = ORCHESTRATION_SCHEMA_VERSION
    run_id: RunId
    expected_revision: int | None = Field(default=None, ge=0)
    context_requests: tuple[ContextSelectionRequest, ...]
    worktree: WorktreeCreationRequest
    builder_request: AgentRequest
    reviewer_request: AgentRequest
    local_repair_request: AgentRequest
    codex_repair_request: AgentRequest | None = None
    validation_plans: tuple[ValidationPlan, ...]
    codex_repair_authorized: bool = False

    @model_validator(mode="after")
    def _validate_request(self) -> Self:
        if not self.context_requests:
            raise ValueError("orchestration requires deterministic context requests")
        if not 1 <= len(self.validation_plans) <= MAX_ORCHESTRATION_PLANS:
            raise ValueError("orchestration requires 1 to 101 validation plans")
        work_package = self.builder_request.work_package_id
        context_roles = tuple(item.role for item in self.context_requests)
        required_roles = {AgentRole.BUILDER, AgentRole.REVIEWER}
        if self.codex_repair_request is not None:
            required_roles.add(AgentRole.REPAIRER)
        if (
            set(context_roles) != required_roles
            or tuple(sorted(context_roles, key=lambda item: item.value)) != context_roles
        ):
            raise ValueError("context requests must be sorted and cover every agent role")
        if any(
            item.run_id != self.run_id
            or item.work_package_id != work_package
            or item.root != self.worktree.source_path
            for item in self.context_requests
        ):
            raise ValueError("context requests must match run, package, and source worktree")
        prototypes = (
            (self.builder_request, AgentRole.BUILDER),
            (self.reviewer_request, AgentRole.REVIEWER),
            (self.local_repair_request, AgentRole.BUILDER),
        )
        for request, role in prototypes:
            if (
                request.run_id != self.run_id
                or request.work_package_id != work_package
                or request.role is not role
                or request.workspace.reference_id != self.worktree.worktree_id.root
            ):
                raise ValueError("agent request prototype correlation, role, or workspace mismatch")
        if self.codex_repair_request is not None and (
            self.codex_repair_request.run_id != self.run_id
            or self.codex_repair_request.work_package_id != work_package
            or self.codex_repair_request.role is not AgentRole.REPAIRER
            or self.codex_repair_request.workspace.reference_id != self.worktree.worktree_id.root
        ):
            raise ValueError("Codex repair prototype correlation or role mismatch")
        if self.codex_repair_authorized and self.codex_repair_request is None:
            raise ValueError("Codex repair authorization requires a REPAIRER prototype")
        if self.worktree.run_id != self.run_id.root:
            raise ValueError("worktree request does not belong to the orchestration run")
        if any(
            plan.run_id != self.run_id
            or plan.work_package_id != work_package
            or plan.workspace.root != self.worktree.target_path
            for plan in self.validation_plans
        ):
            raise ValueError("validation plans must match run, work package, and worktree")
        if len({str(plan.id) for plan in self.validation_plans}) != len(self.validation_plans):
            raise ValueError("validation plan IDs must be unique across attempts")
        return self


class OrchestrationResult(_OrchestrationModel):
    schema_version: Literal[1] = ORCHESTRATION_SCHEMA_VERSION
    status: OrchestrationStatus
    run: Run
    revision: int = Field(ge=0)
    records: tuple[OrchestrationRecord, ...]
    limit_outcome: LimitOutcome = LimitOutcome.NONE
    reason: Annotated[str, Field(min_length=1, max_length=1_024)]

    @model_validator(mode="after")
    def _validate_result(self) -> Self:
        mapping = {
            RunState.APPROVED: OrchestrationStatus.APPROVED,
            RunState.FAILED: OrchestrationStatus.FAILED,
            RunState.BLOCKED: OrchestrationStatus.BLOCKED,
            RunState.CANCELLED: OrchestrationStatus.CANCELLED,
        }
        if self.run.state.is_terminal and self.status is not mapping[self.run.state]:
            raise ValueError("orchestration status does not match terminal run state")
        if len(self.records) > MAX_ORCHESTRATION_RECORDS:
            raise ValueError("orchestration result contains too many records")
        return self


class RecordWriteResult(_OrchestrationModel):
    record: OrchestrationRecord
    created: bool


class OrchestrationJournal(Protocol):
    def list_orchestration_records(self, run_id: RunId) -> tuple[OrchestrationRecord, ...]: ...

    def persist_orchestration_record(
        self, expected: StoredRun, record: OrchestrationRecord
    ) -> RecordWriteResult: ...


class OrchestrationClock(Protocol):
    def now(self) -> datetime: ...


class OrchestrationIdFactory(Protocol):
    def attempt_id(
        self, run_id: RunId, step: OrchestrationStep, sequence: int
    ) -> OrchestrationAttemptId: ...

    def agent_attempt_id(self, attempt_id: OrchestrationAttemptId) -> AgentAttemptId: ...

    def invocation_id(self, attempt_id: OrchestrationAttemptId) -> AgentInvocationId: ...

    def record_id(
        self, attempt_id: OrchestrationAttemptId, stage: OrchestrationRecordStage
    ) -> OrchestrationRecordId: ...


class LocalEvidenceCollector(Protocol):
    def collect(
        self,
        *,
        validation_plan: ValidationPlan,
        validation_result: ValidationPlanResult,
        reviewer_response: AgentResponse,
        observed_at: datetime,
    ) -> LocalApprovalEvidence: ...


def canonical_orchestration_bytes(
    value: OrchestrationRecord | OrchestrationResult | RepairDecision,
) -> bytes:
    return json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def orchestration_digest(value: BaseModel) -> str:
    encoded = json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_agent_attempt(
    attempt: BuildAttempt | ReviewAttempt | RepairAttempt, role: AgentRole
) -> None:
    request = attempt.request
    if (
        request.run_id != attempt.run_id
        or request.work_package_id != attempt.work_package_id
        or request.role is not role
        or request.attempt_id != attempt.agent_attempt_id
        or request.invocation_id != attempt.invocation_id
    ):
        raise ValueError("agent attempt request correlation mismatch")
    if request.attempt_number != attempt.sequence or attempt.adapter_id.root == "":
        raise ValueError("agent attempt sequence or adapter identity mismatch")
    if attempt.status is AttemptStatus.INTENDED and attempt.response is not None:
        raise ValueError("agent intent cannot carry a response")
    if attempt.response is not None and (
        attempt.response.run_id != request.run_id
        or attempt.response.work_package_id != request.work_package_id
        or attempt.response.role is not role
        or attempt.response.attempt_id != request.attempt_id
        or attempt.response.invocation_id != request.invocation_id
        or attempt.response.identity.adapter_id != attempt.adapter_id
    ):
        raise ValueError("agent response correlation mismatch")
    if attempt.status is AttemptStatus.COMPLETED and (
        attempt.response is None or attempt.response.status is not AgentStatus.COMPLETED
    ):
        raise ValueError("completed agent attempt requires completed response")


def agent_side_effect_state(value: SideEffectState) -> ReconciliationState:
    return {
        SideEffectState.NONE: ReconciliationState.KNOWN_NONE,
        SideEffectState.CONFIRMED: ReconciliationState.KNOWN_PRESENT,
        SideEffectState.POSSIBLE: ReconciliationState.AMBIGUOUS,
    }[value]


def validation_attempt_status(result: ValidationPlanResult) -> AttemptStatus:
    if result.status in {ValidationStatus.PASSED, ValidationStatus.PASSED_WITH_ADVISORIES}:
        return AttemptStatus.COMPLETED
    if result.status is ValidationStatus.CANCELLED:
        return AttemptStatus.CANCELLED
    if result.status in {ValidationStatus.BLOCKED, ValidationStatus.UNAVAILABLE}:
        return AttemptStatus.BLOCKED
    if result.status is ValidationStatus.INVALID:
        return AttemptStatus.INVALID
    return AttemptStatus.FAILED
