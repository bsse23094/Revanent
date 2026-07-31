"""Provider-neutral, immutable contracts for agent invocation."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Annotated, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator

from revanent.domain.identifiers import (
    AgentAttemptId,
    AgentInvocationId,
    RunId,
    WorkPackageId,
)
from revanent.domain.models import ReviewResult
from revanent.ports.commands import CancellationToken

AGENT_SCHEMA_VERSION: Literal[1] = 1
MAX_AGENT_OUTPUT_BYTES = 1 * 1_024 * 1_024
MAX_AGENT_TEXT_BYTES = 64 * 1_024
MAX_AGENT_COLLECTION_ITEMS = 256
MAX_AGENT_JSON_DEPTH = 24
MAX_AGENT_ARTIFACT_BYTES = 64 * 1_024 * 1_024
MAX_AGENT_TIMEOUT_SECONDS = 86_400
MAX_AGENT_SCOPE_ITEMS = 256
MAX_AGENT_CONTEXT_ITEMS = 64
MAX_AGENT_ENVIRONMENT_NAMES = 64

_SAFE_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_CONTENT_TYPE = re.compile(r"^[a-z0-9][a-z0-9.+-]{0,63}/[a-z0-9][a-z0-9.+-]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class _AgentModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=True,
        ser_json_bytes="base64",
        val_json_bytes="base64",
    )


class _SafeIdentifier(RootModel[str]):
    model_config = ConfigDict(frozen=True, strict=True)

    @model_validator(mode="after")
    def _validate_root(self) -> Self:
        if _SAFE_ID.fullmatch(self.root) is None:
            raise ValueError("identifier must use a lowercase safe spelling")
        return self

    def __str__(self) -> str:
        return self.root


class ProviderId(_SafeIdentifier):
    """Locally validated provider identity, independent of a model name."""


class AdapterId(_SafeIdentifier):
    """Locally validated adapter implementation identity."""


class ScenarioId(_SafeIdentifier):
    """Stable identity for a deterministic fake-agent scenario."""


class RepositoryPath(RootModel[str]):
    """Canonical repository-relative path used for evidence claims."""

    model_config = ConfigDict(frozen=True, strict=True)

    @model_validator(mode="after")
    def _validate_root(self) -> Self:
        _validate_relative_spelling(self.root, allow_patterns=False)
        return self

    def __str__(self) -> str:
        return self.root


class ScopePath(RootModel[str]):
    """Canonical repository-relative path or bounded glob used for request scope."""

    model_config = ConfigDict(frozen=True, strict=True)

    @model_validator(mode="after")
    def _validate_root(self) -> Self:
        _validate_relative_spelling(self.root, allow_patterns=True)
        return self

    def __str__(self) -> str:
        return self.root


def _validate_relative_spelling(value: str, *, allow_patterns: bool) -> None:
    if not value or len(value.encode("utf-8")) > 512 or "\x00" in value:
        raise ValueError("repository-relative paths must contain 1 to 512 UTF-8 bytes")
    if "\\" in value:
        raise ValueError("repository-relative paths must use forward slashes")
    windows = PureWindowsPath(value)
    posix = PurePosixPath(value)
    if windows.is_absolute() or windows.drive or posix.is_absolute():
        raise ValueError("repository paths must be relative")
    raw_parts = value.split("/")
    if value in {".", ".."} or any(part in {"", ".", ".."} for part in raw_parts):
        raise ValueError("repository paths must be normalized and cannot traverse")
    if not allow_patterns and any(character in value for character in "*?[]"):
        raise ValueError("evidence paths cannot contain glob syntax")


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("timestamp must be timezone-aware UTC")
    return value


def _require_utf8_bytes(value: str, *, maximum: int, label: str) -> str:
    encoded = value.encode("utf-8")
    if not encoded or len(encoded) > maximum or "\x00" in value:
        raise ValueError(f"{label} must contain 1 to {maximum} UTF-8 bytes and no nulls")
    return value


def _require_sorted_unique(values: tuple[object, ...], *, label: str, key: object) -> None:
    normalized = [key(value) for value in values]  # type: ignore[operator]
    if normalized != sorted(normalized):
        raise ValueError(f"{label} must be in canonical sorted order")
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{label} must be unique")


class AgentRole(StrEnum):
    BUILDER = "BUILDER"
    REVIEWER = "REVIEWER"
    REPAIRER = "REPAIRER"


class AgentAvailability(StrEnum):
    AVAILABLE = "AVAILABLE"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


class AgentStatus(StrEnum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    INVALID_OUTPUT = "INVALID_OUTPUT"
    UNAVAILABLE = "UNAVAILABLE"


class StructuredParseStatus(StrEnum):
    PARSED = "PARSED"
    FAILED = "FAILED"
    NOT_PROVIDED = "NOT_PROVIDED"


class AgentFailureCategory(StrEnum):
    UNSUPPORTED_CAPABILITY = "UNSUPPORTED_CAPABILITY"
    ADAPTER_UNAVAILABLE = "ADAPTER_UNAVAILABLE"
    INVALID_REQUEST = "INVALID_REQUEST"
    INVOCATION_FAILURE = "INVOCATION_FAILURE"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    MALFORMED_OUTPUT = "MALFORMED_OUTPUT"
    SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
    CORRELATION_MISMATCH = "CORRELATION_MISMATCH"
    TIMEOUT = "TIMEOUT"
    CANCELLATION = "CANCELLATION"
    EXTERNAL_BLOCKER = "EXTERNAL_BLOCKER"
    ARTIFACT_FAILURE = "ARTIFACT_FAILURE"
    INTERNAL_ADAPTER_FAILURE = "INTERNAL_ADAPTER_FAILURE"


class RetryDisposition(StrEnum):
    RETRYABLE = "RETRYABLE"
    NOT_RETRYABLE = "NOT_RETRYABLE"
    UNKNOWN = "UNKNOWN"


class SideEffectState(StrEnum):
    NONE = "NONE"
    POSSIBLE = "POSSIBLE"
    CONFIRMED = "CONFIRMED"


class AgentArtifactKind(StrEnum):
    PUBLIC_OUTPUT = "PUBLIC_OUTPUT"
    RAW_OUTPUT = "RAW_OUTPUT"
    DIAGNOSTIC = "DIAGNOSTIC"
    CONTEXT = "CONTEXT"
    REVIEW = "REVIEW"
    IMPLEMENTATION = "IMPLEMENTATION"


class AgentArtifactStatus(StrEnum):
    COMPLETE = "COMPLETE"
    TRUNCATED = "TRUNCATED"


class AgentDiagnosticSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class AgentUsageSource(StrEnum):
    REPORTED = "REPORTED"


class WorkspaceKind(StrEnum):
    WORKSPACE = "WORKSPACE"
    WORKTREE = "WORKTREE"


class AgentContextAuthority(StrEnum):
    REVANENT_SYSTEM_POLICY = "REVANENT_SYSTEM_POLICY"
    REPOSITORY_GOVERNANCE = "REPOSITORY_GOVERNANCE"
    TASK_INSTRUCTION = "TASK_INSTRUCTION"
    LOCAL_DETERMINISTIC_EVIDENCE = "LOCAL_DETERMINISTIC_EVIDENCE"
    REPOSITORY_CONTENT = "REPOSITORY_CONTENT"
    PROVIDER_CLAIM = "PROVIDER_CLAIM"


class AgentContextTrust(StrEnum):
    TRUSTED_CONTROL = "TRUSTED_CONTROL"
    TRUSTED_LOCAL_EVIDENCE = "TRUSTED_LOCAL_EVIDENCE"
    REPOSITORY_GOVERNANCE = "REPOSITORY_GOVERNANCE"
    UNTRUSTED_REPOSITORY = "UNTRUSTED_REPOSITORY"
    UNTRUSTED_TEST = "UNTRUSTED_TEST"
    UNTRUSTED_PROVIDER = "UNTRUSTED_PROVIDER"
    UNTRUSTED_DIAGNOSTIC = "UNTRUSTED_DIAGNOSTIC"


class CapabilityMetadata(_AgentModel):
    key: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")]
    value: Annotated[str, Field(min_length=1, max_length=256)]


class AgentCapabilities(_AgentModel):
    """Versioned, explicit facts used for local request routing."""

    schema_version: Literal[1] = AGENT_SCHEMA_VERSION
    provider_id: ProviderId
    adapter_id: AdapterId
    adapter_version: Annotated[str, Field(min_length=1, max_length=64)]
    supported_roles: tuple[AgentRole, ...]
    supports_structured_output: bool
    supports_read_only: bool
    supports_repository_writes: bool
    supports_cancellation: bool
    supports_timeout: bool
    supports_usage_reporting: bool
    supports_artifact_references: bool
    supports_repair: bool
    availability: AgentAvailability
    reason: Annotated[str, Field(min_length=1, max_length=512)] | None = None
    detected_model: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    metadata: tuple[CapabilityMetadata, ...] = ()

    @model_validator(mode="after")
    def _validate_capabilities(self) -> Self:
        if not self.supported_roles:
            raise ValueError("at least one supported agent role is required")
        _require_sorted_unique(
            self.supported_roles, label="supported roles", key=lambda item: item.value
        )
        if len(self.metadata) > 64:
            raise ValueError("capability metadata is limited to 64 entries")
        _require_sorted_unique(
            self.metadata, label="capability metadata", key=lambda item: item.key
        )
        if self.supports_repair != (AgentRole.REPAIRER in self.supported_roles):
            raise ValueError("repair capability must agree with REPAIRER role support")
        if not (self.supports_read_only or self.supports_repository_writes):
            raise ValueError("an adapter must support read-only or repository-write operation")
        if self.availability is AgentAvailability.AVAILABLE and self.reason is not None:
            raise ValueError("available capabilities cannot carry an unavailable/degraded reason")
        if self.availability is not AgentAvailability.AVAILABLE and self.reason is None:
            raise ValueError("unavailable or degraded capabilities require a reason")
        return self


class ExpectedAgentCapabilities(_AgentModel):
    requires_structured_output: bool = True
    requires_read_only: bool = False
    requires_repository_writes: bool = False
    requires_cancellation: bool = False
    requires_timeout: bool = True
    requires_usage_reporting: bool = False
    requires_artifact_references: bool = False
    requires_repair: bool = False

    @model_validator(mode="after")
    def _validate_access_mode(self) -> Self:
        if self.requires_read_only == self.requires_repository_writes:
            raise ValueError("a request must require exactly one access mode")
        return self


class WorkspaceReference(_AgentModel):
    kind: WorkspaceKind
    reference_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
    root: Path

    @field_validator("root")
    @classmethod
    def _validate_root(cls, value: Path) -> Path:
        if not value.is_absolute() or ".." in value.parts or "\x00" in str(value):
            raise ValueError("workspace root must be an absolute normalized path")
        return value


class AgentArtifactReference(_AgentModel):
    """Reference below a separately authorized Revanent-owned artifact root."""

    schema_version: Literal[1] = AGENT_SCHEMA_VERSION
    root_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
    relative_path: RepositoryPath
    kind: AgentArtifactKind
    content_type: Annotated[str, Field(min_length=3, max_length=128)]
    status: AgentArtifactStatus
    observed_bytes: int = Field(ge=0, le=MAX_AGENT_ARTIFACT_BYTES)
    stored_bytes: int = Field(ge=0, le=MAX_AGENT_ARTIFACT_BYTES)
    redacted: bool
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None

    @model_validator(mode="after")
    def _validate_artifact(self) -> Self:
        if _CONTENT_TYPE.fullmatch(self.content_type) is None:
            raise ValueError("artifact content type must be a canonical media type")
        if self.stored_bytes > self.observed_bytes:
            raise ValueError("stored artifact bytes cannot exceed observed bytes")
        if self.status is AgentArtifactStatus.COMPLETE and self.stored_bytes != self.observed_bytes:
            raise ValueError("complete artifacts must retain every observed byte")
        if (
            self.status is AgentArtifactStatus.TRUNCATED
            and self.stored_bytes >= self.observed_bytes
        ):
            raise ValueError("truncated artifacts must omit observed bytes")
        if self.sha256 is not None and _SHA256.fullmatch(self.sha256) is None:
            raise ValueError("artifact integrity must be lowercase SHA-256")
        if self.kind is AgentArtifactKind.RAW_OUTPUT and not self.redacted:
            raise ValueError("raw provider output references must be redacted")
        return self


class ContextReference(_AgentModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=False,
    )

    reference_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
    purpose: Annotated[str, Field(min_length=1, max_length=256)]
    artifact: AgentArtifactReference | None = None
    content: Annotated[str, Field(max_length=262_144)] | None = None
    authority: AgentContextAuthority | None = None
    trust: AgentContextTrust | None = None
    content_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    source_bytes: int | None = Field(default=None, ge=0, le=2_097_152)
    retained_bytes: int | None = Field(default=None, ge=0, le=262_144)
    complete: bool | None = None
    redacted: bool | None = None

    @model_validator(mode="after")
    def _validate_context(self) -> Self:
        if (self.artifact is None) == (self.content is None):
            raise ValueError("context references require exactly one artifact or inline content")
        inline_metadata = (
            self.authority,
            self.trust,
            self.content_sha256,
            self.source_bytes,
            self.retained_bytes,
            self.complete,
            self.redacted,
        )
        if self.content is None:
            if any(value is not None for value in inline_metadata):
                raise ValueError("artifact context cannot carry inline-content metadata")
            return self
        if any(value is None for value in inline_metadata):
            raise ValueError("inline context requires complete provenance and byte metadata")
        assert self.source_bytes is not None
        assert self.retained_bytes is not None
        assert self.complete is not None
        encoded = self.content.encode("utf-8")
        if len(encoded) != self.retained_bytes:
            raise ValueError("inline context retained byte count is inconsistent")
        if hashlib.sha256(encoded).hexdigest() != self.content_sha256:
            raise ValueError("inline context digest is inconsistent")
        if self.complete and self.source_bytes != self.retained_bytes:
            raise ValueError("complete inline context must retain all authorized bytes")
        if not self.complete and self.source_bytes <= self.retained_bytes:
            raise ValueError("truncated inline context must omit authorized bytes")
        return self


class PriorFindingReference(_AgentModel):
    reference_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
    artifact: AgentArtifactReference


class AgentResponseContract(_AgentModel):
    schema_version: Literal[1] = AGENT_SCHEMA_VERSION
    requires_structured_payload: bool = True
    max_public_text_bytes: int = Field(default=16_384, ge=1, le=MAX_AGENT_TEXT_BYTES)
    max_artifacts: int = Field(default=32, ge=0, le=MAX_AGENT_CONTEXT_ITEMS)
    max_diagnostics: int = Field(default=64, ge=0, le=MAX_AGENT_CONTEXT_ITEMS)


class AgentArtifactPolicy(_AgentModel):
    artifact_root_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
    allow_artifact_references: bool = True
    allow_raw_output_reference: bool = False
    max_artifact_bytes: int = Field(default=8 * 1_024 * 1_024, ge=1, le=MAX_AGENT_ARTIFACT_BYTES)
    require_redaction: bool = True


class AgentRouting(_AgentModel):
    provider_id: ProviderId | None = None
    model: Annotated[str, Field(min_length=1, max_length=128)] | None = None


class AgentRequest(_AgentModel):
    """Immutable version-1 request containing provider-neutral authority only."""

    schema_version: Literal[1] = AGENT_SCHEMA_VERSION
    invocation_id: AgentInvocationId
    run_id: RunId
    work_package_id: WorkPackageId
    attempt_id: AgentAttemptId
    attempt_number: int = Field(ge=1, le=100)
    role: AgentRole
    objective: Annotated[str, Field(min_length=1, max_length=8_192)]
    allowed_scope: tuple[ScopePath, ...]
    forbidden_scope: tuple[ScopePath, ...] = ()
    workspace: WorkspaceReference
    context: tuple[ContextReference, ...] = ()
    response_contract: AgentResponseContract = Field(default_factory=AgentResponseContract)
    timeout_seconds: int = Field(ge=1, le=MAX_AGENT_TIMEOUT_SECONDS)
    cancellation_reference: (
        Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")] | None
    ) = None
    artifact_policy: AgentArtifactPolicy
    routing: AgentRouting = Field(default_factory=AgentRouting)
    allowed_environment_names: tuple[
        Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")], ...
    ] = ()
    expected_capabilities: ExpectedAgentCapabilities
    prior_findings: tuple[PriorFindingReference, ...] = ()

    @field_validator("objective")
    @classmethod
    def _validate_objective(cls, value: str) -> str:
        return _require_utf8_bytes(value, maximum=8_192, label="objective")

    @model_validator(mode="after")
    def _validate_request(self) -> Self:
        if not self.allowed_scope or len(self.allowed_scope) > MAX_AGENT_SCOPE_ITEMS:
            raise ValueError(f"allowed scope must contain 1 to {MAX_AGENT_SCOPE_ITEMS} entries")
        if len(self.forbidden_scope) > MAX_AGENT_SCOPE_ITEMS:
            raise ValueError(f"forbidden scope is limited to {MAX_AGENT_SCOPE_ITEMS} entries")
        _require_sorted_unique(
            self.allowed_scope, label="allowed scope", key=lambda item: item.root
        )
        _require_sorted_unique(
            self.forbidden_scope, label="forbidden scope", key=lambda item: item.root
        )
        overlap = {item.root for item in self.allowed_scope} & {
            item.root for item in self.forbidden_scope
        }
        if overlap:
            raise ValueError("allowed and forbidden scope cannot overlap")
        if len(self.context) > MAX_AGENT_CONTEXT_ITEMS:
            raise ValueError(f"context is limited to {MAX_AGENT_CONTEXT_ITEMS} references")
        _require_sorted_unique(
            self.context, label="context references", key=lambda item: item.reference_id
        )
        if sum(item.retained_bytes or 0 for item in self.context) > MAX_AGENT_OUTPUT_BYTES:
            raise ValueError("inline context exceeds the aggregate agent request byte limit")
        if len(self.allowed_environment_names) > MAX_AGENT_ENVIRONMENT_NAMES:
            raise ValueError(
                f"environment-name allowlist is limited to {MAX_AGENT_ENVIRONMENT_NAMES} entries"
            )
        if any(
            _ENVIRONMENT_NAME.fullmatch(name) is None for name in self.allowed_environment_names
        ):
            raise ValueError("environment names must use portable process-variable spelling")
        _require_sorted_unique(
            self.allowed_environment_names, label="environment names", key=lambda item: item
        )
        _require_sorted_unique(
            self.prior_findings,
            label="prior finding references",
            key=lambda item: item.reference_id,
        )
        if len(self.prior_findings) > MAX_AGENT_CONTEXT_ITEMS:
            raise ValueError(f"prior findings are limited to {MAX_AGENT_CONTEXT_ITEMS} references")
        if self.cancellation_reference is not None and not (
            self.expected_capabilities.requires_cancellation
        ):
            raise ValueError("a cancellation reference requires cancellation capability")
        if self.role is AgentRole.REVIEWER:
            if not self.expected_capabilities.requires_read_only:
                raise ValueError("reviewer requests must require read-only capability")
            if self.expected_capabilities.requires_repository_writes:
                raise ValueError("reviewer requests cannot grant repository writes")
            if self.expected_capabilities.requires_repair:
                raise ValueError("reviewer requests cannot require repair capability")
        elif self.role is AgentRole.BUILDER:
            if not self.expected_capabilities.requires_repository_writes:
                raise ValueError("builder requests must require repository-write capability")
            if self.expected_capabilities.requires_repair:
                raise ValueError("builder requests cannot require repair capability")
        else:
            if not self.expected_capabilities.requires_repository_writes:
                raise ValueError("repairer requests must require repository-write capability")
            if not self.expected_capabilities.requires_repair:
                raise ValueError("repairer requests must require repair capability")
        if self.role is not AgentRole.REPAIRER and self.prior_findings:
            raise ValueError("prior findings are permitted only on repairer requests")
        return self


class AgentProviderIdentity(_AgentModel):
    provider_id: ProviderId
    adapter_id: AdapterId
    adapter_version: Annotated[str, Field(min_length=1, max_length=64)]
    model: Annotated[str, Field(min_length=1, max_length=128)] | None = None


class AgentFailure(_AgentModel):
    category: AgentFailureCategory
    code: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")]
    message: Annotated[str, Field(min_length=1, max_length=1_024)]
    retry: RetryDisposition
    side_effects: SideEffectState

    @field_validator("message")
    @classmethod
    def _validate_message(cls, value: str) -> str:
        return _require_utf8_bytes(value, maximum=1_024, label="failure message")

    @model_validator(mode="after")
    def _validate_retry_safety(self) -> Self:
        if (
            self.retry is RetryDisposition.RETRYABLE
            and self.side_effects is not SideEffectState.NONE
        ):
            raise ValueError("retryable failures must prove that no side effect occurred")
        if (
            self.category
            in {
                AgentFailureCategory.UNSUPPORTED_CAPABILITY,
                AgentFailureCategory.ADAPTER_UNAVAILABLE,
                AgentFailureCategory.INVALID_REQUEST,
            }
            and self.side_effects is not SideEffectState.NONE
        ):
            raise ValueError("pre-invocation failures cannot claim possible side effects")
        return self


class AgentDiagnostic(_AgentModel):
    severity: AgentDiagnosticSeverity
    code: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")]
    message: Annotated[str, Field(min_length=1, max_length=2_048)]
    path: RepositoryPath | None = None


class ClaimedCommand(_AgentModel):
    """A bounded command claim, not verified command evidence or raw arguments."""

    capability: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")]
    outcome: Annotated[str, Field(min_length=1, max_length=256)]


class AgentUsage(_AgentModel):
    """Directly reported usage only; P5-002 owns budget enforcement."""

    source: Literal[AgentUsageSource.REPORTED] = AgentUsageSource.REPORTED
    input_tokens: int | None = Field(default=None, ge=0, le=1_000_000_000_000)
    output_tokens: int | None = Field(default=None, ge=0, le=1_000_000_000_000)
    total_tokens: int | None = Field(default=None, ge=0, le=1_000_000_000_000)

    @model_validator(mode="after")
    def _validate_totals(self) -> Self:
        if self.input_tokens is None and self.output_tokens is None and self.total_tokens is None:
            raise ValueError("usage must report at least one token value")
        if (
            self.input_tokens is not None
            and self.output_tokens is not None
            and self.total_tokens is not None
            and self.input_tokens + self.output_tokens != self.total_tokens
        ):
            raise ValueError("reported total tokens must equal input plus output tokens")
        return self


class BuilderPayload(_AgentModel):
    role: Literal[AgentRole.BUILDER] = AgentRole.BUILDER
    implementation_summary: Annotated[str, Field(min_length=1, max_length=8_192)]
    files_inspected: tuple[RepositoryPath, ...] = ()
    files_claimed_changed: tuple[RepositoryPath, ...] = ()
    commands_claimed_run: tuple[ClaimedCommand, ...] = ()

    @model_validator(mode="after")
    def _validate_evidence(self) -> Self:
        _validate_payload_claims(self.files_inspected, self.files_claimed_changed)
        if len(self.commands_claimed_run) > MAX_AGENT_COLLECTION_ITEMS:
            raise ValueError("command claims exceed the hard collection limit")
        return self


class ReviewerPayload(_AgentModel):
    role: Literal[AgentRole.REVIEWER] = AgentRole.REVIEWER
    review: ReviewResult
    files_inspected: tuple[RepositoryPath, ...] = ()

    @model_validator(mode="after")
    def _validate_evidence(self) -> Self:
        if len(self.files_inspected) > MAX_AGENT_COLLECTION_ITEMS:
            raise ValueError("file claims exceed the hard collection limit")
        _require_sorted_unique(
            self.files_inspected, label="files inspected", key=lambda item: item.root
        )
        return self


class RepairerPayload(_AgentModel):
    role: Literal[AgentRole.REPAIRER] = AgentRole.REPAIRER
    repair_summary: Annotated[str, Field(min_length=1, max_length=8_192)]
    files_inspected: tuple[RepositoryPath, ...] = ()
    files_claimed_changed: tuple[RepositoryPath, ...] = ()
    commands_claimed_run: tuple[ClaimedCommand, ...] = ()
    addressed_finding_references: tuple[
        Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")], ...
    ] = ()

    @model_validator(mode="after")
    def _validate_evidence(self) -> Self:
        _validate_payload_claims(self.files_inspected, self.files_claimed_changed)
        if len(self.commands_claimed_run) > MAX_AGENT_COLLECTION_ITEMS:
            raise ValueError("command claims exceed the hard collection limit")
        if len(self.addressed_finding_references) > MAX_AGENT_CONTEXT_ITEMS:
            raise ValueError("addressed findings exceed the hard collection limit")
        _require_sorted_unique(
            self.addressed_finding_references,
            label="addressed finding references",
            key=lambda item: item,
        )
        return self


def _validate_payload_claims(
    inspected: tuple[RepositoryPath, ...], changed: tuple[RepositoryPath, ...]
) -> None:
    if len(inspected) > MAX_AGENT_COLLECTION_ITEMS or len(changed) > MAX_AGENT_COLLECTION_ITEMS:
        raise ValueError("file claims exceed the hard collection limit")
    _require_sorted_unique(inspected, label="files inspected", key=lambda item: item.root)
    _require_sorted_unique(changed, label="files claimed changed", key=lambda item: item.root)


AgentPayload = Annotated[
    BuilderPayload | ReviewerPayload | RepairerPayload,
    Field(discriminator="role"),
]


class AgentResponse(_AgentModel):
    """Normalized outcome evidence; every provider claim remains unverified."""

    schema_version: Literal[1] = AGENT_SCHEMA_VERSION
    invocation_id: AgentInvocationId
    run_id: RunId
    work_package_id: WorkPackageId
    attempt_id: AgentAttemptId
    attempt_number: int = Field(ge=1, le=100)
    role: AgentRole
    expected_response_schema_version: Literal[1] = AGENT_SCHEMA_VERSION
    status: AgentStatus
    started_at: datetime
    completed_at: datetime
    duration_ms: int = Field(ge=0, le=MAX_AGENT_TIMEOUT_SECONDS * 1_000)
    summary: Annotated[str, Field(min_length=1, max_length=2_048)]
    public_text: Annotated[str, Field(max_length=MAX_AGENT_TEXT_BYTES)] = ""
    structured_parse_status: StructuredParseStatus
    payload: AgentPayload | None = None
    diagnostics: tuple[AgentDiagnostic, ...] = ()
    artifacts: tuple[AgentArtifactReference, ...] = ()
    usage: AgentUsage | None = None
    identity: AgentProviderIdentity
    failure: AgentFailure | None = None
    raw_output_artifact: AgentArtifactReference | None = None

    _timestamps_utc = field_validator("started_at", "completed_at")(_require_utc)

    @field_validator("summary")
    @classmethod
    def _validate_summary(cls, value: str) -> str:
        return _require_utf8_bytes(value, maximum=2_048, label="response summary")

    @field_validator("public_text")
    @classmethod
    def _validate_public_text(cls, value: str) -> str:
        if "\x00" in value or len(value.encode("utf-8")) > MAX_AGENT_TEXT_BYTES:
            raise ValueError(f"public text is limited to {MAX_AGENT_TEXT_BYTES} UTF-8 bytes")
        return value

    @model_validator(mode="after")
    def _validate_response(self) -> Self:
        if self.completed_at < self.started_at:
            raise ValueError("agent completion cannot precede its start")
        if self.completed_at - self.started_at != timedelta(milliseconds=self.duration_ms):
            raise ValueError("duration_ms must exactly match response timestamps")
        _require_sorted_unique(
            self.diagnostics,
            label="diagnostics",
            key=lambda item: (item.code, item.path.root if item.path else ""),
        )
        _require_sorted_unique(
            self.artifacts,
            label="artifact references",
            key=lambda item: (item.root_id, item.relative_path.root),
        )
        if len(self.diagnostics) > MAX_AGENT_CONTEXT_ITEMS:
            raise ValueError("diagnostics exceed the hard collection limit")
        if len(self.artifacts) > MAX_AGENT_CONTEXT_ITEMS:
            raise ValueError("artifact references exceed the hard collection limit")
        if self.raw_output_artifact is not None:
            if self.raw_output_artifact.kind is not AgentArtifactKind.RAW_OUTPUT:
                raise ValueError("raw output reference must use RAW_OUTPUT artifact kind")
            if not self.raw_output_artifact.redacted:
                raise ValueError("raw output references must be redacted")
        if self.status is AgentStatus.COMPLETED:
            if self.failure is not None or self.payload is None:
                raise ValueError("completed responses require payload and no failure")
            if self.structured_parse_status is not StructuredParseStatus.PARSED:
                raise ValueError(
                    "completed responses require successfully parsed structured output"
                )
            if self.payload.role is not self.role:
                raise ValueError("payload role must match response role")
        else:
            if self.failure is None or self.payload is not None:
                raise ValueError("non-completed responses require failure and no payload")
        expected_categories = {
            AgentStatus.BLOCKED: {AgentFailureCategory.EXTERNAL_BLOCKER},
            AgentStatus.TIMED_OUT: {AgentFailureCategory.TIMEOUT},
            AgentStatus.CANCELLED: {AgentFailureCategory.CANCELLATION},
            AgentStatus.INVALID_OUTPUT: {
                AgentFailureCategory.MALFORMED_OUTPUT,
                AgentFailureCategory.SCHEMA_MISMATCH,
                AgentFailureCategory.CORRELATION_MISMATCH,
            },
            AgentStatus.UNAVAILABLE: {AgentFailureCategory.ADAPTER_UNAVAILABLE},
        }
        if (
            self.failure is not None
            and self.status in expected_categories
            and self.failure.category not in expected_categories[self.status]
        ):
            raise ValueError("agent status and failure category do not agree")
        failed_categories = {
            AgentFailureCategory.UNSUPPORTED_CAPABILITY,
            AgentFailureCategory.INVALID_REQUEST,
            AgentFailureCategory.INVOCATION_FAILURE,
            AgentFailureCategory.PROVIDER_FAILURE,
            AgentFailureCategory.ARTIFACT_FAILURE,
            AgentFailureCategory.INTERNAL_ADAPTER_FAILURE,
        }
        if (
            self.status is AgentStatus.FAILED
            and self.failure is not None
            and self.failure.category not in failed_categories
        ):
            raise ValueError(
                "FAILED status requires an invocation, provider, artifact, or adapter failure"
            )
        if self.status is AgentStatus.INVALID_OUTPUT and (
            self.structured_parse_status is not StructuredParseStatus.FAILED
        ):
            raise ValueError("invalid output must carry FAILED parse status")
        return self

    @property
    def succeeded(self) -> bool:
        return self.status is AgentStatus.COMPLETED


class AgentOutputLimits(_AgentModel):
    max_input_bytes: int = Field(default=MAX_AGENT_OUTPUT_BYTES, ge=1, le=MAX_AGENT_OUTPUT_BYTES)
    max_depth: int = Field(default=MAX_AGENT_JSON_DEPTH, ge=1, le=MAX_AGENT_JSON_DEPTH)
    max_collection_items: int = Field(
        default=MAX_AGENT_COLLECTION_ITEMS, ge=1, le=MAX_AGENT_COLLECTION_ITEMS
    )


class AgentAdapter(Protocol):
    """Application-facing provider-neutral adapter contract."""

    @property
    def capabilities(self) -> AgentCapabilities: ...

    def invoke(
        self, request: AgentRequest, *, cancellation: CancellationToken | None = None
    ) -> AgentResponse: ...
