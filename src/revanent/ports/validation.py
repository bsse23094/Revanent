"""Provider-neutral contracts for deterministic validation evidence."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator

from revanent.domain.identifiers import RunId, WorkPackageId
from revanent.ports.agents import RepositoryPath, WorkspaceReference
from revanent.ports.commands import (
    ArtifactStatus,
    CancellationToken,
    CommandFailureCategory,
    CommandStatus,
    EnvironmentOverrides,
    OutputStream,
)

VALIDATION_SCHEMA_VERSION: Literal[1] = 1
MAX_VALIDATION_COMMANDS = 64
MAX_VALIDATION_ARGUMENTS = 256
MAX_VALIDATION_ARGUMENT_BYTES = 32 * 1_024
MAX_VALIDATION_OUTPUT_BYTES = 1 * 1_024 * 1_024
MAX_VALIDATION_ARTIFACT_BYTES = 64 * 1_024 * 1_024

_PLAN_ID = re.compile(r"^vplan_[0-9a-f]{32}$")
_COMMAND_ID = re.compile(r"^vcmd_[a-z0-9][a-z0-9_.-]{0,57}$")
_EXECUTABLE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.,:+()/-]{0,127}$")
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_CORRELATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_CREDENTIAL_ARGUMENT = re.compile(
    r"(?:api[_-]?key|authorization|credential|password|passwd|secret|token)\s*[:=]",
    re.IGNORECASE,
)


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("validation timestamp must be timezone-aware UTC")
    return value


class _ValidationModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=True,
    )


class ValidationPlanId(RootModel[str]):
    model_config = ConfigDict(frozen=True, strict=True)

    @model_validator(mode="after")
    def _validate_root(self) -> Self:
        if _PLAN_ID.fullmatch(self.root) is None:
            raise ValueError("validation plan ID must use vplan_ plus 32 lowercase hex characters")
        return self

    def __str__(self) -> str:
        return self.root


class ValidationCommandId(RootModel[str]):
    model_config = ConfigDict(frozen=True, strict=True)

    @model_validator(mode="after")
    def _validate_root(self) -> Self:
        if _COMMAND_ID.fullmatch(self.root) is None:
            raise ValueError("validation command ID must use the canonical vcmd_ spelling")
        return self

    def __str__(self) -> str:
        return self.root


class ValidationCommandClass(StrEnum):
    REQUIRED = "REQUIRED"
    ADVISORY = "ADVISORY"


class ValidationStatus(StrEnum):
    PASSED = "PASSED"
    PASSED_WITH_ADVISORIES = "PASSED_WITH_ADVISORIES"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID = "INVALID"
    NOT_RUN = "NOT_RUN"


class ValidationFailureCategory(StrEnum):
    COMMAND_FAILED = "COMMAND_FAILED"
    TIMEOUT = "TIMEOUT"
    CANCELLATION = "CANCELLATION"
    EXECUTABLE_UNAVAILABLE = "EXECUTABLE_UNAVAILABLE"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    LAUNCH_BLOCKED = "LAUNCH_BLOCKED"
    ARTIFACT = "ARTIFACT"
    INTERNAL = "INTERNAL"
    INVALID_EVIDENCE = "INVALID_EVIDENCE"
    NOT_RUN = "NOT_RUN"


class ValidationExecutionPolicy(_ValidationModel):
    fail_fast: bool = False
    allow_advisory_failures: bool = False


class ValidationOutputPolicy(_ValidationModel):
    stdout_bytes: int = Field(default=256 * 1_024, ge=1, le=MAX_VALIDATION_OUTPUT_BYTES)
    stderr_bytes: int = Field(default=256 * 1_024, ge=1, le=MAX_VALIDATION_OUTPUT_BYTES)
    artifact_bytes_per_stream: int = Field(
        default=8 * 1_024 * 1_024, ge=1, le=MAX_VALIDATION_ARTIFACT_BYTES
    )
    capture_artifacts: bool = False
    require_complete_stdout: bool = False
    require_complete_stderr: bool = False


class ValidationArtifactPolicy(_ValidationModel):
    root_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
    directory: Path | None = None
    allow_artifacts: bool = False
    require_redaction: bool = True

    @model_validator(mode="after")
    def _validate_policy(self) -> Self:
        if self.allow_artifacts != (self.directory is not None):
            raise ValueError("artifact allowance requires exactly one explicit artifact directory")
        if self.directory is not None and (
            not self.directory.is_absolute() or ".." in self.directory.parts
        ):
            raise ValueError("validation artifact directory must be an absolute normalized path")
        if not self.require_redaction:
            raise ValueError("validation artifacts must require redaction")
        return self


class ValidationCommand(_ValidationModel):
    id: ValidationCommandId
    name: Annotated[str, Field(min_length=1, max_length=128)]
    executable: Annotated[str, Field(min_length=1, max_length=128)]
    arguments: tuple[str, ...] = ()
    relative_working_directory: RepositoryPath | None = None
    classification: ValidationCommandClass = ValidationCommandClass.REQUIRED
    security_critical: bool = False
    expected_exit_codes: tuple[int, ...] = (0,)
    timeout_seconds: int = Field(default=300, ge=1, le=86_400)
    output: ValidationOutputPolicy = Field(default_factory=ValidationOutputPolicy)
    allowed_environment_names: tuple[str, ...] = ()

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if _SAFE_NAME.fullmatch(value) is None or "\x00" in value:
            raise ValueError("validation command name uses unsupported characters")
        return value

    @field_validator("executable")
    @classmethod
    def _validate_executable(cls, value: str) -> str:
        if _EXECUTABLE.fullmatch(value) is None or any(mark in value for mark in ("/", "\\")):
            raise ValueError("validation executable must be an approved simple capability name")
        return value

    @model_validator(mode="after")
    def _validate_command(self) -> Self:
        if len(self.arguments) > MAX_VALIDATION_ARGUMENTS:
            raise ValueError("validation command has too many arguments")
        size = 0
        for argument in self.arguments:
            if "\x00" in argument or _CREDENTIAL_ARGUMENT.search(argument):
                raise ValueError(
                    "validation arguments cannot contain nulls or credential assignments"
                )
            size += len(argument.encode("utf-8"))
        if size > MAX_VALIDATION_ARGUMENT_BYTES:
            raise ValueError("validation argument bytes exceed the hard limit")
        if (
            not self.expected_exit_codes
            or tuple(sorted(self.expected_exit_codes)) != self.expected_exit_codes
            or len(set(self.expected_exit_codes)) != len(self.expected_exit_codes)
            or any(
                type(code) is not int or not 0 <= code <= 2**31 - 1
                for code in self.expected_exit_codes
            )
        ):
            raise ValueError("expected exit codes must be sorted, unique, and non-negative")
        if (
            tuple(sorted(self.allowed_environment_names)) != self.allowed_environment_names
            or len(set(self.allowed_environment_names)) != len(self.allowed_environment_names)
            or any(
                _ENVIRONMENT_NAME.fullmatch(name) is None for name in self.allowed_environment_names
            )
        ):
            raise ValueError("validation environment names must be sorted, unique, and portable")
        if self.security_critical and self.classification is not ValidationCommandClass.REQUIRED:
            raise ValueError("security-critical validation commands must be required")
        return self


class ValidationPlan(_ValidationModel):
    schema_version: Literal[1] = VALIDATION_SCHEMA_VERSION
    id: ValidationPlanId
    run_id: RunId
    work_package_id: WorkPackageId
    created_at: datetime
    workspace: WorkspaceReference
    commands: tuple[ValidationCommand, ...]
    execution: ValidationExecutionPolicy = Field(default_factory=ValidationExecutionPolicy)
    artifacts: ValidationArtifactPolicy

    @field_validator("created_at")
    @classmethod
    def _validate_timestamp(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def _validate_plan(self) -> Self:
        if not 1 <= len(self.commands) <= MAX_VALIDATION_COMMANDS:
            raise ValueError("validation plan must contain 1 to 64 commands")
        identifiers = [str(command.id) for command in self.commands]
        names = [command.name for command in self.commands]
        signatures = [
            (
                command.executable,
                command.arguments,
                str(command.relative_working_directory)
                if command.relative_working_directory is not None
                else "",
            )
            for command in self.commands
        ]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("validation command IDs must be unique")
        if len(names) != len(set(names)):
            raise ValueError("validation command names must be unique")
        if len(signatures) != len(set(signatures)):
            raise ValueError("duplicate validation command signatures are not permitted")
        if any(command.output.capture_artifacts for command in self.commands) and not (
            self.artifacts.allow_artifacts
        ):
            raise ValueError("command artifact capture requires plan artifact authorization")
        if not any(
            command.classification is ValidationCommandClass.REQUIRED for command in self.commands
        ):
            raise ValueError("validation plan must contain at least one required command")
        return self


class ValidationFailure(_ValidationModel):
    category: ValidationFailureCategory
    code: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")]
    message: Annotated[str, Field(min_length=1, max_length=1_024)]
    command_failure_category: CommandFailureCategory | None = None


class ValidationArtifactReference(_ValidationModel):
    schema_version: Literal[1] = VALIDATION_SCHEMA_VERSION
    root_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
    relative_path: RepositoryPath
    stream: OutputStream
    status: ArtifactStatus
    correlation_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")]
    observed_source_bytes: int = Field(ge=0, le=MAX_VALIDATION_ARTIFACT_BYTES)
    source_bytes_retained: int = Field(ge=0, le=MAX_VALIDATION_ARTIFACT_BYTES)
    redacted_bytes_observed: int = Field(ge=0, le=MAX_VALIDATION_ARTIFACT_BYTES)
    stored_bytes: int = Field(ge=0, le=MAX_VALIDATION_ARTIFACT_BYTES)
    redacted: Literal[True] = True

    @model_validator(mode="after")
    def _validate_artifact(self) -> Self:
        if self.source_bytes_retained > self.observed_source_bytes:
            raise ValueError("artifact source retention exceeds observed source bytes")
        if self.stored_bytes > self.redacted_bytes_observed:
            raise ValueError("artifact stored bytes exceed the redacted representation")
        complete = (
            self.source_bytes_retained == self.observed_source_bytes
            and self.stored_bytes == self.redacted_bytes_observed
        )
        if self.status is ArtifactStatus.COMPLETE and not complete:
            raise ValueError("complete validation artifacts must retain complete evidence")
        if self.status is ArtifactStatus.TRUNCATED and complete:
            raise ValueError("truncated validation artifacts must omit evidence bytes")
        return self


class ValidationCapturedOutput(_ValidationModel):
    text: Annotated[str, Field(max_length=MAX_VALIDATION_OUTPUT_BYTES)] = ""
    observed_bytes: int = Field(ge=0, le=MAX_VALIDATION_ARTIFACT_BYTES)
    retained_bytes: int = Field(ge=0, le=MAX_VALIDATION_OUTPUT_BYTES)
    truncated: bool
    redaction_truncated: bool = False
    artifact: ValidationArtifactReference | None = None

    @field_validator("text")
    @classmethod
    def _validate_text(cls, value: str) -> str:
        if "\x00" in value or len(value.encode("utf-8")) > MAX_VALIDATION_OUTPUT_BYTES:
            raise ValueError("validation output exceeds the bounded UTF-8 representation")
        return value

    @model_validator(mode="after")
    def _validate_output(self) -> Self:
        if self.retained_bytes > self.observed_bytes:
            raise ValueError("retained validation output exceeds observed bytes")
        if self.truncated != (self.retained_bytes < self.observed_bytes):
            raise ValueError("validation output truncation counters disagree")
        if self.artifact is not None and not (self.truncated or self.redaction_truncated):
            raise ValueError("validation artifact requires truncated source or representation")
        return self


class ValidationCommandResult(_ValidationModel):
    schema_version: Literal[1] = VALIDATION_SCHEMA_VERSION
    plan_id: ValidationPlanId
    run_id: RunId
    work_package_id: WorkPackageId
    command_id: ValidationCommandId
    sequence: int = Field(ge=1, le=MAX_VALIDATION_COMMANDS)
    classification: ValidationCommandClass
    status: ValidationStatus
    command_status: CommandStatus | None
    started_at: datetime
    completed_at: datetime
    duration_ms: int = Field(ge=0, le=86_400_000)
    executable: Annotated[str, Field(min_length=1, max_length=128)]
    resolved_executable: Path | None = None
    correlation_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")]
    exit_code: int | None = Field(default=None, ge=0, le=2**32 - 1)
    expected_exit_codes: tuple[int, ...]
    stdout: ValidationCapturedOutput
    stderr: ValidationCapturedOutput
    failure: ValidationFailure | None = None

    _timestamps_utc = field_validator("started_at", "completed_at")(_require_utc)

    @field_validator("resolved_executable")
    @classmethod
    def _validate_resolved_executable(cls, value: Path | None) -> Path | None:
        if value is not None and not value.is_absolute():
            raise ValueError("resolved validation executable identity must be absolute")
        return value

    @model_validator(mode="after")
    def _validate_result(self) -> Self:
        if self.completed_at < self.started_at:
            raise ValueError("validation command completion precedes its start")
        if self.completed_at - self.started_at != timedelta(milliseconds=self.duration_ms):
            raise ValueError("validation command duration must match timestamps exactly")
        if self.status in {ValidationStatus.PASSED, ValidationStatus.PASSED_WITH_ADVISORIES}:
            if (
                self.status is not ValidationStatus.PASSED
                or self.command_status is not CommandStatus.SUCCESS
                or self.exit_code not in self.expected_exit_codes
                or self.failure is not None
            ):
                raise ValueError("passed command evidence is internally inconsistent")
        elif self.failure is None:
            raise ValueError("non-passing validation command evidence requires a failure")
        if self.status is ValidationStatus.NOT_RUN:
            if (
                self.command_status is not None
                or self.exit_code is not None
                or self.duration_ms != 0
            ):
                raise ValueError("not-run evidence cannot claim process execution")
        elif self.status is ValidationStatus.CANCELLED and self.command_status is None:
            if self.exit_code is not None or self.duration_ms != 0:
                raise ValueError("prelaunch cancellation cannot claim process execution")
        elif self.status is ValidationStatus.INVALID and self.command_status is None:
            if self.exit_code is not None or self.duration_ms != 0:
                raise ValueError("prelaunch invalid evidence cannot claim process execution")
        elif self.command_status is None:
            raise ValueError("attempted validation evidence requires a command status")
        for output in (self.stdout, self.stderr):
            if (
                output.artifact is not None
                and output.artifact.correlation_id != self.correlation_id
            ):
                raise ValueError("validation artifact correlation does not match its command")
        return self


class ValidationSummary(_ValidationModel):
    total: int = Field(ge=1, le=MAX_VALIDATION_COMMANDS)
    passed: int = Field(ge=0, le=MAX_VALIDATION_COMMANDS)
    failed: int = Field(ge=0, le=MAX_VALIDATION_COMMANDS)
    timed_out: int = Field(ge=0, le=MAX_VALIDATION_COMMANDS)
    cancelled: int = Field(ge=0, le=MAX_VALIDATION_COMMANDS)
    blocked: int = Field(ge=0, le=MAX_VALIDATION_COMMANDS)
    unavailable: int = Field(ge=0, le=MAX_VALIDATION_COMMANDS)
    invalid: int = Field(ge=0, le=MAX_VALIDATION_COMMANDS)
    not_run: int = Field(ge=0, le=MAX_VALIDATION_COMMANDS)

    @model_validator(mode="after")
    def _validate_total(self) -> Self:
        counted = (
            self.passed
            + self.failed
            + self.timed_out
            + self.cancelled
            + self.blocked
            + self.unavailable
            + self.invalid
            + self.not_run
        )
        if counted != self.total:
            raise ValueError("validation summary counters must equal total commands")
        return self


class ValidationPlanResult(_ValidationModel):
    schema_version: Literal[1] = VALIDATION_SCHEMA_VERSION
    plan_schema_version: Literal[1] = VALIDATION_SCHEMA_VERSION
    plan_id: ValidationPlanId
    run_id: RunId
    work_package_id: WorkPackageId
    started_at: datetime
    completed_at: datetime
    status: ValidationStatus
    commands: tuple[ValidationCommandResult, ...]
    summary: ValidationSummary
    required_commands_passed: bool
    all_evidence_complete: bool
    advisory_failures_accepted: bool
    cancelled: bool

    _timestamps_utc = field_validator("started_at", "completed_at")(_require_utc)

    @model_validator(mode="after")
    def _validate_aggregate(self) -> Self:
        if self.completed_at < self.started_at or not self.commands:
            raise ValueError("validation aggregate requires ordered command evidence")
        if tuple(item.sequence for item in self.commands) != tuple(
            range(1, len(self.commands) + 1)
        ):
            raise ValueError("validation aggregate command sequence is not canonical")
        if len({str(item.command_id) for item in self.commands}) != len(self.commands):
            raise ValueError("validation aggregate command IDs must be unique")
        if any(
            item.plan_id != self.plan_id
            or item.run_id != self.run_id
            or item.work_package_id != self.work_package_id
            for item in self.commands
        ):
            raise ValueError("validation command correlation differs from its aggregate")
        expected = validation_summary(self.commands)
        if self.summary != expected:
            raise ValueError("validation summary does not match command evidence")
        if self.cancelled != any(
            item.status is ValidationStatus.CANCELLED for item in self.commands
        ):
            raise ValueError("validation cancellation flag does not match command evidence")
        if self.status is ValidationStatus.PASSED and not (
            self.required_commands_passed
            and self.all_evidence_complete
            and self.summary.passed == self.summary.total
            and not self.advisory_failures_accepted
        ):
            raise ValueError("passed validation aggregate is internally inconsistent")
        if self.status is ValidationStatus.PASSED_WITH_ADVISORIES and not (
            self.required_commands_passed
            and self.all_evidence_complete
            and self.summary.failed > 0
            and self.advisory_failures_accepted
        ):
            raise ValueError("advisory validation aggregate is internally inconsistent")
        if self.status in {
            ValidationStatus.PASSED,
            ValidationStatus.PASSED_WITH_ADVISORIES,
        } and (not self.required_commands_passed or not self.all_evidence_complete):
            raise ValueError("approvable validation requires complete required evidence")
        return self

    @property
    def approvable(self) -> bool:
        return (
            self.status
            in {
                ValidationStatus.PASSED,
                ValidationStatus.PASSED_WITH_ADVISORIES,
            }
            and self.required_commands_passed
            and self.all_evidence_complete
        )


class ValidationEvidenceError(ValueError):
    """Typed fail-closed rejection of mismatched aggregate evidence."""


class ValidationExecutor(Protocol):
    def execute(
        self,
        plan: ValidationPlan,
        *,
        started_at: datetime,
        cancellation: CancellationToken | None = None,
        environment: EnvironmentOverrides | None = None,
    ) -> ValidationPlanResult: ...


def validation_summary(
    results: tuple[ValidationCommandResult, ...],
) -> ValidationSummary:
    return ValidationSummary(
        total=len(results),
        passed=sum(item.status is ValidationStatus.PASSED for item in results),
        failed=sum(item.status is ValidationStatus.FAILED for item in results),
        timed_out=sum(item.status is ValidationStatus.TIMED_OUT for item in results),
        cancelled=sum(item.status is ValidationStatus.CANCELLED for item in results),
        blocked=sum(item.status is ValidationStatus.BLOCKED for item in results),
        unavailable=sum(item.status is ValidationStatus.UNAVAILABLE for item in results),
        invalid=sum(item.status is ValidationStatus.INVALID for item in results),
        not_run=sum(item.status is ValidationStatus.NOT_RUN for item in results),
    )


def canonical_validation_bytes(model: ValidationPlan | ValidationPlanResult) -> bytes:
    return json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def validation_correlation_id(plan_id: ValidationPlanId, command_id: ValidationCommandId) -> str:
    value = f"{plan_id}.{command_id}"
    if _CORRELATION_ID.fullmatch(value) is None:
        raise ValueError("validation correlation identifier is not command-safe")
    return value
