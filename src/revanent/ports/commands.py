"""Provider-independent contracts for controlled local commands."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

COMMAND_SCHEMA_VERSION = 1
MAX_ARGUMENTS = 1_024
MAX_ARGUMENT_BYTES = 64 * 1_024
MAX_ENVIRONMENT_ENTRIES = 128
MAX_ENVIRONMENT_VALUE_BYTES = 32 * 1_024
MAX_STDIN_BYTES = 1 * 1_024 * 1_024
MAX_STREAM_CAPTURE_BYTES = 16 * 1_024 * 1_024
MAX_STREAM_ARTIFACT_BYTES = 64 * 1_024 * 1_024
MAX_TIMEOUT_SECONDS = 86_400.0

_CORRELATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class CommandStatus(StrEnum):
    """One deterministic terminal outcome for a command request."""

    SUCCESS = "SUCCESS"
    NONZERO_EXIT = "NONZERO_EXIT"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    LAUNCH_FAILED = "LAUNCH_FAILED"
    POLICY_REJECTED = "POLICY_REJECTED"
    OUTPUT_ARTIFACT_FAILED = "OUTPUT_ARTIFACT_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class CommandFailureCategory(StrEnum):
    """Sanitized failure categories used by orchestration decisions."""

    POLICY = "POLICY"
    EXECUTABLE_UNAVAILABLE = "EXECUTABLE_UNAVAILABLE"
    WORKING_DIRECTORY = "WORKING_DIRECTORY"
    ENVIRONMENT = "ENVIRONMENT"
    LAUNCH = "LAUNCH"
    TIMEOUT = "TIMEOUT"
    CANCELLATION = "CANCELLATION"
    OUTPUT_ARTIFACT = "OUTPUT_ARTIFACT"
    INTERNAL = "INTERNAL"


class ArtifactStatus(StrEnum):
    """Completeness of one redacted overflow artifact."""

    COMPLETE = "COMPLETE"
    TRUNCATED = "TRUNCATED"


class OutputStream(StrEnum):
    """Command stream identity used by bounded output and artifact references."""

    STDOUT = "stdout"
    STDERR = "stderr"


class CommandPolicyError(Exception):
    """Base class for sanitized, expected policy failures."""

    category = CommandFailureCategory.POLICY


class ExecutablePolicyError(CommandPolicyError):
    """The executable name or resolved identity is not authorized."""


class ExecutableUnavailableError(CommandPolicyError):
    """No configured executable candidate is currently usable."""

    category = CommandFailureCategory.EXECUTABLE_UNAVAILABLE


class WorkingDirectoryPolicyError(CommandPolicyError):
    """A path or working directory is outside its approved roots."""

    category = CommandFailureCategory.WORKING_DIRECTORY


class EnvironmentPolicyError(CommandPolicyError):
    """A child environment key or value is not permitted."""

    category = CommandFailureCategory.ENVIRONMENT


class OutputArtifactPolicyError(CommandPolicyError):
    """An overflow artifact target is not an approved directory."""

    category = CommandFailureCategory.OUTPUT_ARTIFACT


class CancellationToken(Protocol):
    """Read-only cooperative cancellation signal accepted by the runner."""

    def is_cancelled(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class OutputLimits:
    """Independent byte bounds for retained and artifact stream data."""

    stdout_bytes: int = 256 * 1_024
    stderr_bytes: int = 256 * 1_024
    artifact_bytes_per_stream: int = 8 * 1_024 * 1_024

    def __post_init__(self) -> None:
        for name, value in (
            ("stdout_bytes", self.stdout_bytes),
            ("stderr_bytes", self.stderr_bytes),
        ):
            if type(value) is not int or not 1 <= value <= MAX_STREAM_CAPTURE_BYTES:
                raise ValueError(f"{name} must be between 1 and {MAX_STREAM_CAPTURE_BYTES}")
        if (
            type(self.artifact_bytes_per_stream) is not int
            or not 1 <= self.artifact_bytes_per_stream <= MAX_STREAM_ARTIFACT_BYTES
        ):
            raise ValueError(
                f"artifact_bytes_per_stream must be between 1 and {MAX_STREAM_ARTIFACT_BYTES}"
            )


@dataclass(frozen=True, slots=True)
class EnvironmentVariable:
    """One typed per-command child-environment override."""

    key: str
    value: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.key, str)
            or not isinstance(self.value, str)
            or not self.key
            or "=" in self.key
            or "\x00" in self.key
            or "\x00" in self.value
        ):
            raise ValueError("environment keys and values must use valid process syntax")
        if len(self.value.encode("utf-8", errors="surrogatepass")) > (MAX_ENVIRONMENT_VALUE_BYTES):
            raise ValueError(
                f"environment values are limited to {MAX_ENVIRONMENT_VALUE_BYTES} bytes"
            )


@dataclass(frozen=True, slots=True)
class EnvironmentOverrides:
    """Immutable typed collection replacing raw environment dictionaries at the port."""

    variables: tuple[EnvironmentVariable, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        if len(self.variables) > MAX_ENVIRONMENT_ENTRIES:
            raise ValueError(f"environment is limited to {MAX_ENVIRONMENT_ENTRIES} entries")
        if any(not isinstance(variable, EnvironmentVariable) for variable in self.variables):
            raise ValueError("environment overrides must contain typed variables")
        keys = [variable.key for variable in self.variables]
        if len(keys) != len(set(keys)):
            raise ValueError("environment override keys must be unique")
        object.__setattr__(self, "variables", tuple(self.variables))

    @classmethod
    def from_mapping(cls, values: dict[str, str]) -> EnvironmentOverrides:
        """Convert configuration input before crossing the application port."""
        return cls(tuple(EnvironmentVariable(key, value) for key, value in values.items()))

    def to_mapping(self) -> dict[str, str]:
        """Materialize only inside infrastructure for child-process construction."""
        return {variable.key: variable.value for variable in self.variables}


@dataclass(frozen=True, slots=True)
class CommandRequest:
    """Immutable version-1 request; executable and arguments never share a string."""

    executable: str
    arguments: tuple[str, ...] = field(repr=False)
    working_directory: Path
    correlation_id: str
    environment: EnvironmentOverrides = field(default_factory=EnvironmentOverrides, repr=False)
    timeout_seconds: float = 300.0
    stdin: bytes | None = field(default=None, repr=False)
    output_limits: OutputLimits = field(default_factory=OutputLimits)
    cancellation: CancellationToken | None = field(default=None, repr=False, compare=False)
    expected_exit_codes: tuple[int, ...] = (0,)
    artifact_directory: Path | None = None
    schema_version: int = COMMAND_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != COMMAND_SCHEMA_VERSION:
            raise ValueError(f"unsupported command request schema version {self.schema_version}")
        if (
            not isinstance(self.executable, str)
            or not self.executable
            or not self.executable.strip()
            or "\x00" in self.executable
        ):
            raise ValueError("executable must be a non-empty name without null bytes")
        if len(self.arguments) > MAX_ARGUMENTS:
            raise ValueError(f"arguments are limited to {MAX_ARGUMENTS} values")
        argument_bytes = 0
        for argument in self.arguments:
            if not isinstance(argument, str) or "\x00" in argument:
                raise ValueError("arguments must be strings without null bytes")
            argument_bytes += len(argument.encode("utf-8", errors="surrogatepass"))
        if argument_bytes > MAX_ARGUMENT_BYTES:
            raise ValueError(f"argument data is limited to {MAX_ARGUMENT_BYTES} bytes")
        if not isinstance(self.working_directory, Path):
            raise TypeError("working_directory must be a pathlib.Path")
        if not _CORRELATION_ID.fullmatch(self.correlation_id):
            raise ValueError("correlation_id must contain only safe identifier characters")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, int | float)
            or not math.isfinite(self.timeout_seconds)
            or not 0 < self.timeout_seconds <= MAX_TIMEOUT_SECONDS
        ):
            raise ValueError(f"timeout_seconds must be positive and at most {MAX_TIMEOUT_SECONDS}")
        if self.stdin is not None:
            if not isinstance(self.stdin, bytes):
                raise ValueError("stdin must be bytes when provided")
            if len(self.stdin) > MAX_STDIN_BYTES:
                raise ValueError(f"stdin is limited to {MAX_STDIN_BYTES} bytes")
        if not isinstance(self.environment, EnvironmentOverrides):
            raise TypeError("environment must use typed EnvironmentOverrides")
        if not self.expected_exit_codes or len(set(self.expected_exit_codes)) != len(
            self.expected_exit_codes
        ):
            raise ValueError("expected_exit_codes must be non-empty and unique")
        if any(
            type(code) is not int or not 0 <= code <= 2**32 - 1 for code in self.expected_exit_codes
        ):
            raise ValueError("expected exit codes must be unsigned 32-bit integers")
        if self.artifact_directory is not None and not isinstance(self.artifact_directory, Path):
            raise TypeError("artifact_directory must be a pathlib.Path")
        object.__setattr__(self, "arguments", tuple(self.arguments))
        object.__setattr__(self, "expected_exit_codes", tuple(self.expected_exit_codes))


@dataclass(frozen=True, slots=True)
class CommandFailure:
    """A deliberately low-detail failure safe for logs and reports."""

    category: CommandFailureCategory
    message: str

    def __post_init__(self) -> None:
        if not self.message or "\x00" in self.message:
            raise ValueError("command failure messages must be non-empty and contain no null bytes")


@dataclass(frozen=True, slots=True)
class ArtifactReference:
    """Reference to one atomically written, redacted, bounded output artifact."""

    path: Path
    stream: OutputStream
    status: ArtifactStatus
    observed_bytes: int
    source_bytes_retained: int
    redacted_bytes_observed: int
    stored_bytes: int

    def __post_init__(self) -> None:
        if not self.path.is_absolute():
            raise ValueError("artifact reference paths must be absolute")
        if (
            min(
                self.observed_bytes,
                self.source_bytes_retained,
                self.redacted_bytes_observed,
                self.stored_bytes,
            )
            < 0
        ):
            raise ValueError("artifact byte counters cannot be negative")
        if self.source_bytes_retained > self.observed_bytes:
            raise ValueError("artifact source bytes cannot exceed observed bytes")
        if self.stored_bytes > self.redacted_bytes_observed:
            raise ValueError("stored artifact bytes cannot exceed redacted bytes observed")
        is_complete = (
            self.source_bytes_retained == self.observed_bytes
            and self.stored_bytes == self.redacted_bytes_observed
        )
        if self.status is ArtifactStatus.COMPLETE and not is_complete:
            raise ValueError("complete artifacts must retain every observed source byte")
        if self.status is ArtifactStatus.TRUNCATED and is_complete:
            raise ValueError("truncated artifacts must omit source or representation bytes")


@dataclass(frozen=True, slots=True)
class CapturedOutput:
    """Sanitized retained output and accounting measured before decoding."""

    text: str
    observed_bytes: int
    retained_bytes: int
    truncated: bool
    redaction_truncated: bool = False
    artifact: ArtifactReference | None = None

    def __post_init__(self) -> None:
        if self.observed_bytes < 0 or self.retained_bytes < 0:
            raise ValueError("captured output byte counters cannot be negative")
        if self.retained_bytes > self.observed_bytes:
            raise ValueError("retained output bytes cannot exceed observed bytes")
        if self.truncated != (self.retained_bytes < self.observed_bytes):
            raise ValueError("captured output truncation must match its byte counters")
        if self.artifact is not None and not (self.truncated or self.redaction_truncated):
            raise ValueError("an overflow artifact requires truncated source or representation")


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Normalized terminal command result returned on every expected failure path."""

    correlation_id: str
    executable: str
    resolved_executable: Path | None
    status: CommandStatus
    started_at: datetime
    completed_at: datetime
    duration_seconds: float
    stdout: CapturedOutput
    stderr: CapturedOutput
    exit_code: int | None = None
    failure: CommandFailure | None = None
    schema_version: int = COMMAND_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != COMMAND_SCHEMA_VERSION:
            raise ValueError(f"unsupported command result schema version {self.schema_version}")
        for timestamp in (self.started_at, self.completed_at):
            if timestamp.tzinfo is None or timestamp.utcoffset() != UTC.utcoffset(timestamp):
                raise ValueError("command result timestamps must be timezone-aware UTC")
        if self.completed_at < self.started_at:
            raise ValueError("command completion cannot precede its start")
        if not math.isfinite(self.duration_seconds) or self.duration_seconds < 0:
            raise ValueError("command duration must be finite and non-negative")
        process_statuses = {
            CommandStatus.SUCCESS,
            CommandStatus.NONZERO_EXIT,
            CommandStatus.TIMEOUT,
            CommandStatus.CANCELLED,
            CommandStatus.OUTPUT_ARTIFACT_FAILED,
        }
        if self.status in {CommandStatus.SUCCESS, CommandStatus.NONZERO_EXIT}:
            if self.exit_code is None or self.failure is not None:
                raise ValueError("completed process results require an exit code and no failure")
        elif self.failure is None:
            raise ValueError("failed command results require a structured failure")
        if self.status not in process_statuses and self.exit_code is not None:
            raise ValueError("prelaunch failures cannot carry an exit code")

    @property
    def succeeded(self) -> bool:
        return self.status is CommandStatus.SUCCESS


class CommandRunner(Protocol):
    """Application-facing controlled command runner."""

    def run(self, request: CommandRequest) -> CommandResult: ...
