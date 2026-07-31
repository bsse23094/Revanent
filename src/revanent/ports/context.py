"""Strict provider-neutral contracts for deterministic context construction."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from revanent.domain import FindingSeverity, RunId, TaskSpecification, WorkPackageId
from revanent.ports.agents import (
    MAX_AGENT_OUTPUT_BYTES,
    AgentArtifactReference,
    AgentContextAuthority,
    AgentContextTrust,
    AgentRole,
    ContextReference,
    RepositoryPath,
)
from revanent.ports.validation import ValidationPlanResult

CONTEXT_SCHEMA_VERSION: Literal[1] = 1
CONTEXT_SELECTION_POLICY_VERSION: Literal["p5-001-v1"] = "p5-001-v1"


class _ContextModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=False,
    )


class ContextCandidateKind(StrEnum):
    REPOSITORY_FILE = "REPOSITORY_FILE"
    LOCAL_EVIDENCE = "LOCAL_EVIDENCE"
    ARTIFACT = "ARTIFACT"


class ContextSource(StrEnum):
    GOVERNING = "GOVERNING"
    TASK_PATH = "TASK_PATH"
    CHANGED_PATH = "CHANGED_PATH"
    DIFF = "DIFF"
    VALIDATION = "VALIDATION"
    REVIEW = "REVIEW"
    PRIOR_ATTEMPT = "PRIOR_ATTEMPT"
    REPAIR_DECISION = "REPAIR_DECISION"
    DEPENDENCY = "DEPENDENCY"
    TEST = "TEST"
    ARTIFACT = "ARTIFACT"


class ContextImportance(StrEnum):
    REQUIRED = "REQUIRED"
    PREFERRED = "PREFERRED"
    OPTIONAL = "OPTIONAL"


class InclusionReason(StrEnum):
    GOVERNING_INSTRUCTION = "GOVERNING_INSTRUCTION"
    EXPLICIT_TASK_PATH = "EXPLICIT_TASK_PATH"
    CHANGED_PATH = "CHANGED_PATH"
    DIFF_PATH = "DIFF_PATH"
    VALIDATION_FAILURE = "VALIDATION_FAILURE"
    REVIEW_FINDING = "REVIEW_FINDING"
    PRIOR_ATTEMPT = "PRIOR_ATTEMPT"
    UNRESOLVED_DECISION = "UNRESOLVED_DECISION"
    DIRECT_DEPENDENCY = "DIRECT_DEPENDENCY"
    CORRESPONDING_TEST = "CORRESPONDING_TEST"
    APPROVED_ARTIFACT = "APPROVED_ARTIFACT"


class ExclusionReason(StrEnum):
    FORBIDDEN_SCOPE = "FORBIDDEN_SCOPE"
    SCOPE_CONFLICT = "SCOPE_CONFLICT"
    UNSAFE_PATH = "UNSAFE_PATH"
    MISSING = "MISSING"
    SYMLINK_ESCAPE = "SYMLINK_ESCAPE"
    ROOT_MISMATCH = "ROOT_MISMATCH"
    SPECIAL_FILE = "SPECIAL_FILE"
    FILE_CHANGED = "FILE_CHANGED"
    BINARY = "BINARY"
    SECRET = "SECRET"
    UNSUPPORTED_ENCODING = "UNSUPPORTED_ENCODING"
    EXCLUDED_DIRECTORY = "EXCLUDED_DIRECTORY"
    OVERSIZED = "OVERSIZED"
    INCOMPLETE = "INCOMPLETE"
    INVALID_ARTIFACT = "INVALID_ARTIFACT"
    AGGREGATE_LIMIT = "AGGREGATE_LIMIT"
    ITEM_LIMIT = "ITEM_LIMIT"
    DUPLICATE_CONTENT = "DUPLICATE_CONTENT"
    UNSUPPORTED_LANGUAGE = "UNSUPPORTED_LANGUAGE"
    DISCOVERY_LIMIT = "DISCOVERY_LIMIT"
    ROLE_MISMATCH = "ROLE_MISMATCH"


class ContextAuthority(StrEnum):
    REVANENT_SYSTEM_POLICY = "REVANENT_SYSTEM_POLICY"
    REPOSITORY_GOVERNANCE = "REPOSITORY_GOVERNANCE"
    TASK_INSTRUCTION = "TASK_INSTRUCTION"
    LOCAL_DETERMINISTIC_EVIDENCE = "LOCAL_DETERMINISTIC_EVIDENCE"
    REPOSITORY_CONTENT = "REPOSITORY_CONTENT"
    PROVIDER_CLAIM = "PROVIDER_CLAIM"


class ContextTrust(StrEnum):
    TRUSTED_CONTROL = "TRUSTED_CONTROL"
    TRUSTED_LOCAL_EVIDENCE = "TRUSTED_LOCAL_EVIDENCE"
    REPOSITORY_GOVERNANCE = "REPOSITORY_GOVERNANCE"
    UNTRUSTED_REPOSITORY = "UNTRUSTED_REPOSITORY"
    UNTRUSTED_TEST = "UNTRUSTED_TEST"
    UNTRUSTED_PROVIDER = "UNTRUSTED_PROVIDER"
    UNTRUSTED_DIAGNOSTIC = "UNTRUSTED_DIAGNOSTIC"


class ContextContentState(StrEnum):
    COMPLETE = "COMPLETE"
    TRUNCATED = "TRUNCATED"
    REFERENCED = "REFERENCED"


class RedactionState(StrEnum):
    NOT_NEEDED = "NOT_NEEDED"
    REDACTED = "REDACTED"


class ContextSelectionStatus(StrEnum):
    COMPLETE = "COMPLETE"
    COMPLETE_WITH_EXCLUSIONS = "COMPLETE_WITH_EXCLUSIONS"


class BaselineKind(StrEnum):
    AUTHORIZED_CANDIDATES = "AUTHORIZED_CANDIDATES"
    INJECTED_REPOSITORY = "INJECTED_REPOSITORY"


class ContextLimits(_ContextModel):
    max_candidates: int = Field(default=128, ge=1, le=1_024)
    max_items: int = Field(default=64, ge=1, le=256)
    max_exclusions: int = Field(default=128, ge=1, le=512)
    max_source_bytes: int = Field(default=262_144, ge=1, le=2_097_152)
    max_item_bytes: int = Field(default=32_768, ge=32, le=262_144)
    max_total_bytes: int = Field(default=262_144, ge=32, le=2_097_152)
    max_artifact_bytes: int = Field(default=65_536, ge=32, le=2_097_152)
    max_artifact_total_bytes: int = Field(default=131_072, ge=32, le=2_097_152)
    max_dependency_depth: int = Field(default=1, ge=0, le=4)
    max_dependencies: int = Field(default=32, ge=0, le=128)
    max_tests: int = Field(default=32, ge=0, le=128)
    max_test_scan_entries: int = Field(default=2_048, ge=1, le=16_384)
    max_read_retries: int = Field(default=1, ge=0, le=2)


class ContextCandidate(_ContextModel):
    schema_version: Literal[1] = CONTEXT_SCHEMA_VERSION
    path: RepositoryPath
    kind: Literal[ContextCandidateKind.REPOSITORY_FILE] = ContextCandidateKind.REPOSITORY_FILE
    source: ContextSource
    importance: ContextImportance = ContextImportance.PREFERRED
    authority: ContextAuthority = ContextAuthority.REPOSITORY_CONTENT
    trust: ContextTrust = ContextTrust.UNTRUSTED_REPOSITORY
    reasons: tuple[InclusionReason, ...]
    priority: int = Field(ge=0, le=1_000)
    roles: tuple[AgentRole, ...] = (
        AgentRole.BUILDER,
        AgentRole.REPAIRER,
        AgentRole.REVIEWER,
    )
    correlation_ids: tuple[Annotated[str, Field(min_length=1, max_length=128)], ...] = ()
    parent_path: RepositoryPath | None = None
    requires_complete: bool = False

    @model_validator(mode="after")
    def _canonical(self) -> Self:
        _sorted_unique(self.reasons, "context candidate reasons", lambda item: item.value)
        _sorted_unique(self.roles, "context candidate roles", lambda item: item.value)
        _sorted_unique(self.correlation_ids, "context correlations", lambda item: item)
        return self


class InlineContextEvidence(_ContextModel):
    schema_version: Literal[1] = CONTEXT_SCHEMA_VERSION
    evidence_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
    source: ContextSource
    importance: ContextImportance
    authority: ContextAuthority
    trust: ContextTrust
    reasons: tuple[InclusionReason, ...]
    priority: int = Field(ge=0, le=1_000)
    content: Annotated[str, Field(min_length=1, max_length=262_144)]
    path: RepositoryPath | None = None
    roles: tuple[AgentRole, ...] = (
        AgentRole.BUILDER,
        AgentRole.REPAIRER,
        AgentRole.REVIEWER,
    )
    correlation_ids: tuple[Annotated[str, Field(min_length=1, max_length=128)], ...] = ()
    requires_complete: bool = False

    @model_validator(mode="after")
    def _canonical(self) -> Self:
        _sorted_unique(self.reasons, "inline evidence reasons", lambda item: item.value)
        _sorted_unique(self.roles, "inline evidence roles", lambda item: item.value)
        _sorted_unique(self.correlation_ids, "inline evidence correlations", lambda item: item)
        return self


class ValidationContextEvidence(_ContextModel):
    result: ValidationPlanResult
    affected_paths: tuple[RepositoryPath, ...] = ()
    attempt_id: Annotated[str, Field(min_length=1, max_length=128)]

    @model_validator(mode="after")
    def _canonical(self) -> Self:
        _sorted_unique(self.affected_paths, "validation affected paths", lambda item: item.root)
        return self


class ReviewContextEvidence(_ContextModel):
    run_id: RunId
    work_package_id: WorkPackageId
    finding_id: Annotated[str, Field(min_length=1, max_length=128)]
    severity: FindingSeverity
    summary: Annotated[str, Field(min_length=1, max_length=256)]
    required_change: Annotated[str, Field(min_length=1, max_length=2_048)]
    unresolved: bool = True
    path: RepositoryPath | None = None
    correlation_ids: tuple[Annotated[str, Field(min_length=1, max_length=128)], ...] = ()

    @model_validator(mode="after")
    def _canonical(self) -> Self:
        _sorted_unique(self.correlation_ids, "review correlations", lambda item: item)
        return self


class PriorAttemptContextEvidence(_ContextModel):
    run_id: RunId
    work_package_id: WorkPackageId
    attempt_id: Annotated[str, Field(min_length=1, max_length=128)]
    summary: Annotated[str, Field(min_length=1, max_length=2_048)]
    unresolved: bool
    path: RepositoryPath | None = None
    correlation_ids: tuple[Annotated[str, Field(min_length=1, max_length=128)], ...] = ()


class RepairDecisionContextEvidence(_ContextModel):
    run_id: RunId
    work_package_id: WorkPackageId
    decision_id: Annotated[str, Field(min_length=1, max_length=128)]
    summary: Annotated[str, Field(min_length=1, max_length=2_048)]
    unresolved: bool
    correlation_ids: tuple[Annotated[str, Field(min_length=1, max_length=128)], ...] = ()


class GoverningContext(_ContextModel):
    active_work_package: RepositoryPath
    include_agents: bool = True
    include_architecture: bool = True
    include_requirements: bool = True
    include_security: bool = True
    include_workflow: bool = True
    adrs: tuple[RepositoryPath, ...] = ()

    @model_validator(mode="after")
    def _canonical(self) -> Self:
        _sorted_unique(self.adrs, "governing ADRs", lambda item: item.root)
        return self


class ApprovedContextArtifact(_ContextModel):
    reference: AgentArtifactReference
    root: Path
    run_id: RunId
    work_package_id: WorkPackageId
    correlation_id: Annotated[str, Field(min_length=1, max_length=128)]
    importance: ContextImportance = ContextImportance.PREFERRED
    authority: ContextAuthority = ContextAuthority.LOCAL_DETERMINISTIC_EVIDENCE
    trust: ContextTrust = ContextTrust.TRUSTED_LOCAL_EVIDENCE
    requires_complete: bool = True

    @field_validator("root")
    @classmethod
    def _absolute_root(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("artifact root must be absolute")
        return value


class ContextDiscoveryInput(_ContextModel):
    explicit_paths: tuple[RepositoryPath, ...] = ()
    changed_paths: tuple[RepositoryPath, ...] = ()
    diff_paths: tuple[RepositoryPath, ...] = ()
    governing: GoverningContext | None = None
    validation: tuple[ValidationContextEvidence, ...] = ()
    review: tuple[ReviewContextEvidence, ...] = ()
    prior_attempts: tuple[PriorAttemptContextEvidence, ...] = ()
    repair_decisions: tuple[RepairDecisionContextEvidence, ...] = ()
    inline_evidence: tuple[InlineContextEvidence, ...] = ()
    artifacts: tuple[ApprovedContextArtifact, ...] = ()

    @model_validator(mode="after")
    def _identities_are_unambiguous(self) -> Self:
        inline: dict[str, InlineContextEvidence] = {}
        for inline_item in self.inline_evidence:
            if inline_item.evidence_id in inline and inline[inline_item.evidence_id] != inline_item:
                raise ValueError("inline evidence identities cannot conflict")
            inline[inline_item.evidence_id] = inline_item
        artifacts: dict[str, ApprovedContextArtifact] = {}
        for artifact_item in self.artifacts:
            key = f"{artifact_item.reference.root_id}:{artifact_item.reference.relative_path.root}"
            if key in artifacts and artifacts[key] != artifact_item:
                raise ValueError("artifact identities cannot conflict")
            artifacts[key] = artifact_item
        return self


class ContextItem(_ContextModel):
    schema_version: Literal[1] = CONTEXT_SCHEMA_VERSION
    id: Annotated[str, Field(pattern=r"^ctx_[0-9a-f]{32}$")]
    run_id: RunId
    work_package_id: WorkPackageId
    path: RepositoryPath | None = None
    reference: Annotated[str, Field(min_length=1, max_length=512)]
    kind: ContextCandidateKind
    source: ContextSource
    importance: ContextImportance
    authority: ContextAuthority
    trust: ContextTrust
    role: AgentRole
    reasons: tuple[InclusionReason, ...]
    priority: int = Field(ge=0, le=1_000)
    correlation_ids: tuple[str, ...] = ()
    source_bytes: int = Field(ge=0, le=2_097_152)
    retained_bytes: int = Field(ge=0, le=262_144)
    truncated_bytes: int = Field(ge=0, le=2_097_152)
    state: ContextContentState
    source_digest_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    retained_digest_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    redaction: RedactionState
    duplicate_of: Annotated[str, Field(pattern=r"^ctx_[0-9a-f]{32}$")] | None = None
    content: Annotated[str, Field(max_length=262_144)] = ""
    artifact: AgentArtifactReference | None = None

    @model_validator(mode="after")
    def _content_accounting(self) -> Self:
        if len(self.content.encode("utf-8")) != self.retained_bytes:
            raise ValueError("retained context byte count does not match content")
        if (
            self.state is not ContextContentState.REFERENCED
            and self.truncated_bytes != self.source_bytes - self.retained_bytes
        ):
            raise ValueError("truncated byte count must match source minus retained bytes")
        if self.state is ContextContentState.COMPLETE and self.truncated_bytes:
            raise ValueError("complete context cannot omit bytes")
        if self.state is ContextContentState.TRUNCATED and not self.truncated_bytes:
            raise ValueError("truncated context must omit bytes")
        if self.state is ContextContentState.REFERENCED:
            if self.duplicate_of is None or self.retained_bytes or self.truncated_bytes:
                raise ValueError("referenced duplicates require a target and retain no bytes")
        elif self.duplicate_of is not None:
            raise ValueError("only referenced duplicate items may name a duplicate target")
        if hashlib.sha256(self.content.encode("utf-8")).hexdigest() != self.retained_digest_sha256:
            raise ValueError("retained digest does not match public context content")
        return self


class ContextManifestItem(_ContextModel):
    schema_version: Literal[1] = CONTEXT_SCHEMA_VERSION
    sequence: int = Field(ge=1, le=256)
    id: Annotated[str, Field(pattern=r"^ctx_[0-9a-f]{32}$")]
    path: RepositoryPath | None = None
    reference: Annotated[str, Field(min_length=1, max_length=512)]
    kind: ContextCandidateKind
    source: ContextSource
    importance: ContextImportance
    authority: ContextAuthority
    trust: ContextTrust
    role: AgentRole
    reasons: tuple[InclusionReason, ...]
    priority: int = Field(ge=0, le=1_000)
    correlation_ids: tuple[str, ...] = ()
    source_bytes: int = Field(ge=0, le=2_097_152)
    retained_bytes: int = Field(ge=0, le=262_144)
    truncated_bytes: int = Field(ge=0, le=2_097_152)
    state: ContextContentState
    source_digest_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    retained_digest_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    redaction: RedactionState
    duplicate_of: Annotated[str, Field(pattern=r"^ctx_[0-9a-f]{32}$")] | None = None
    artifact: AgentArtifactReference | None = None


class ContextExclusion(_ContextModel):
    schema_version: Literal[1] = CONTEXT_SCHEMA_VERSION
    reference: Annotated[str, Field(min_length=1, max_length=512)]
    path: RepositoryPath | None = None
    reason: ExclusionReason
    importance: ContextImportance
    source: ContextSource
    authority: ContextAuthority
    trust: ContextTrust
    source_bytes: int | None = Field(default=None, ge=0, le=2_097_152)


class ContextManifest(_ContextModel):
    schema_version: Literal[1] = CONTEXT_SCHEMA_VERSION
    manifest_id: Annotated[str, Field(pattern=r"^ctxmanifest_[0-9a-f]{64}$")]
    run_id: RunId
    work_package_id: WorkPackageId
    task_id: Annotated[str, Field(min_length=1, max_length=128)]
    role: AgentRole
    repository_reference: Annotated[str, Field(min_length=1, max_length=128)]
    worktree_reference: Annotated[str, Field(min_length=1, max_length=128)]
    selection_policy_version: Literal["p5-001-v1"] = CONTEXT_SELECTION_POLICY_VERSION
    created_at: datetime
    limits: ContextLimits
    items: tuple[ContextManifestItem, ...]
    exclusions: tuple[ContextExclusion, ...]
    exclusion_overflow_count: int = Field(default=0, ge=0, le=1_024)
    candidate_count: int = Field(ge=0, le=1_024)
    included_count: int = Field(ge=0, le=256)
    excluded_count: int = Field(ge=0, le=1_024)
    required_count: int = Field(ge=0, le=1_024)
    preferred_count: int = Field(ge=0, le=1_024)
    optional_count: int = Field(ge=0, le=1_024)
    total_source_bytes_considered: int = Field(ge=0)
    retained_bytes: int = Field(ge=0)
    excluded_bytes: int = Field(ge=0)
    truncated_bytes: int = Field(ge=0)
    duplicate_bytes_avoided: int = Field(ge=0)
    baseline_kind: BaselineKind
    baseline_bytes: int = Field(ge=0)
    retained_to_baseline_ratio: float = Field(ge=0.0, le=1.0)
    required_evidence_complete: bool
    warnings: tuple[Annotated[str, Field(min_length=1, max_length=256)], ...] = ()
    status: ContextSelectionStatus

    _created_utc = field_validator("created_at")(
        lambda value: _require_utc(value, "context creation timestamp")
    )

    @model_validator(mode="after")
    def _canonical(self) -> Self:
        if self.included_count != len(self.items):
            raise ValueError("manifest included count does not match items")
        if self.excluded_count != len(self.exclusions) + self.exclusion_overflow_count:
            raise ValueError("manifest excluded count does not match exclusion ledger")
        if self.candidate_count != self.included_count + self.excluded_count:
            raise ValueError("manifest candidate accounting is incomplete")
        if self.retained_bytes != sum(item.retained_bytes for item in self.items):
            raise ValueError("manifest retained bytes do not match items")
        if self.truncated_bytes != sum(item.truncated_bytes for item in self.items):
            raise ValueError("manifest truncated bytes do not match items")
        if tuple(item.sequence for item in self.items) != tuple(range(1, len(self.items) + 1)):
            raise ValueError("context item sequences must be contiguous")
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError("context item IDs must be unique")
        if (
            len(self.items) > self.limits.max_items
            or len(self.exclusions) > self.limits.max_exclusions
        ):
            raise ValueError("manifest exceeds item or exclusion limits")
        if self.retained_bytes > self.limits.max_total_bytes:
            raise ValueError("manifest exceeds total retained byte limit")
        expected_ratio = (
            0.0 if self.baseline_bytes == 0 else self.retained_bytes / self.baseline_bytes
        )
        if abs(self.retained_to_baseline_ratio - expected_ratio) > 1e-12:
            raise ValueError("manifest retained-to-baseline ratio is inconsistent")
        _sorted_unique(self.warnings, "manifest warnings", lambda item: item)
        return self


class ContextPackage(_ContextModel):
    schema_version: Literal[1] = CONTEXT_SCHEMA_VERSION
    manifest: ContextManifest
    trusted_controls: tuple[Annotated[str, Field(min_length=1, max_length=8_192)], ...]
    untrusted_items: tuple[ContextItem, ...]
    artifact_references: tuple[AgentArtifactReference, ...] = ()

    @model_validator(mode="after")
    def _matches_manifest(self) -> Self:
        if (
            tuple(_manifest_item(item, index) for index, item in enumerate(self.untrusted_items, 1))
            != self.manifest.items
        ):
            raise ValueError("context package items must match manifest metadata")
        _sorted_unique(
            self.artifact_references,
            "context package artifact references",
            lambda item: f"{item.root_id}:{item.relative_path.root}",
        )
        if (
            sum(item.retained_bytes or 0 for item in self.agent_references())
            > MAX_AGENT_OUTPUT_BYTES
        ):
            raise ValueError("context package exceeds the aggregate agent request byte limit")
        return self

    def agent_references(self) -> tuple[ContextReference, ...]:
        """Project the package through the existing AgentRequest.context field."""
        references: list[ContextReference] = []
        controls = "\n\n".join(self.trusted_controls)
        encoded_controls = controls.encode("utf-8")
        references.append(
            ContextReference(
                reference_id="control.001",
                purpose="trusted Revanent controls",
                content=controls,
                authority=AgentContextAuthority.REVANENT_SYSTEM_POLICY,
                trust=AgentContextTrust.TRUSTED_CONTROL,
                content_sha256=hashlib.sha256(encoded_controls).hexdigest(),
                source_bytes=len(encoded_controls),
                retained_bytes=len(encoded_controls),
                complete=True,
                redacted="[REDACTED]" in controls,
            )
        )
        for item in self.untrusted_items:
            if item.state is ContextContentState.REFERENCED:
                continue
            references.append(
                ContextReference(
                    reference_id=item.id,
                    purpose=f"{item.source.value.lower()} context: {item.reference}",
                    content=item.content,
                    authority=AgentContextAuthority(item.authority.value),
                    trust=AgentContextTrust(item.trust.value),
                    content_sha256=item.retained_digest_sha256,
                    source_bytes=item.source_bytes,
                    retained_bytes=item.retained_bytes,
                    complete=item.state is ContextContentState.COMPLETE,
                    redacted=item.redaction is RedactionState.REDACTED,
                )
            )
        return tuple(sorted(references, key=lambda item: item.reference_id))


class ContextSelectionFailure(_ContextModel):
    code: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")]
    category: ExclusionReason | None = None
    path: RepositoryPath | None = None
    message: Annotated[str, Field(min_length=1, max_length=512)]
    blocking: bool = False


class ContextSelectionResult(_ContextModel):
    schema_version: Literal[1] = CONTEXT_SCHEMA_VERSION
    package: ContextPackage | None = None
    failure: ContextSelectionFailure | None = None

    @model_validator(mode="after")
    def _one_outcome(self) -> Self:
        if (self.package is None) == (self.failure is None):
            raise ValueError("context selection result requires exactly one outcome")
        return self


class ContextSelectionRequest(_ContextModel):
    schema_version: Literal[1] = CONTEXT_SCHEMA_VERSION
    request_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
    run_id: RunId
    work_package_id: WorkPackageId
    task: TaskSpecification
    role: AgentRole
    root: Path
    repository_reference: Annotated[str, Field(min_length=1, max_length=128)]
    worktree_reference: Annotated[str, Field(min_length=1, max_length=128)]
    candidates: tuple[ContextCandidate, ...] = ()
    discovery: ContextDiscoveryInput = Field(default_factory=ContextDiscoveryInput)
    limits: ContextLimits = Field(default_factory=ContextLimits)
    trusted_controls: tuple[Annotated[str, Field(min_length=1, max_length=8_192)], ...]
    created_at: datetime
    baseline_bytes: int | None = Field(default=None, ge=0)

    @field_validator("root")
    @classmethod
    def _absolute_root(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("context root must be absolute")
        return value

    _created_utc = field_validator("created_at")(
        lambda value: _require_utc(value, "context request timestamp")
    )

    @model_validator(mode="after")
    def _bounded(self) -> Self:
        if not self.trusted_controls:
            raise ValueError("context selection requires trusted controls")
        if len(self.candidates) > self.limits.max_candidates:
            raise ValueError("explicit context candidate count exceeds configured limit")
        if self.work_package_id.root == "":
            raise ValueError("work package identity is required")
        return self


def context_item_id(
    run_id: RunId, reference: str, authority: ContextAuthority, trust: ContextTrust
) -> str:
    value = f"{run_id.root}:{reference}:{authority.value}:{trust.value}"
    return "ctx_" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def manifest_id(values: dict[str, object]) -> str:
    encoded = json.dumps(
        values, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")
    return "ctxmanifest_" + hashlib.sha256(encoded).hexdigest()


def canonical_context_bytes(
    value: ContextManifest | ContextPackage | ContextSelectionResult,
) -> bytes:
    return json.dumps(
        value.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _manifest_item(item: ContextItem, sequence: int) -> ContextManifestItem:
    values = item.model_dump(mode="python", exclude={"content", "run_id", "work_package_id"})
    return ContextManifestItem(sequence=sequence, **values)


def _sorted_unique(values: tuple[object, ...], label: str, key: object) -> None:
    normalized = [key(value) for value in values]  # type: ignore[operator]
    if normalized != sorted(normalized) or len(normalized) != len(set(normalized)):
        raise ValueError(f"{label} must be sorted and unique")


def _require_utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{label} must be timezone-aware UTC")
    return value


class ContextSelectorPort(Protocol):
    def select(self, request: ContextSelectionRequest) -> ContextSelectionResult: ...


class ContextRedactorPort(Protocol):
    def redact(self, value: str, *, truncated: bool = False) -> str: ...
