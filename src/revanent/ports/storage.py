"""Provider-independent durable run-state interface and explicit errors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from revanent.domain import EventId, Run, RunEvent, RunId, TransitionResult


class StorageError(Exception):
    """Base class for sanitized durable-storage failures."""


class StoragePathError(StorageError):
    """The configured database path cannot be used safely."""


class StorageNotInitializedError(StorageError):
    """The database does not exist or has no compatible Revanent schema."""


class UnsupportedSchemaVersionError(StorageError):
    """The database was written by a newer unsupported schema."""

    def __init__(self, actual: int, supported: int) -> None:
        self.actual = actual
        self.supported = supported
        super().__init__(
            f"unsupported storage schema version {actual}; supported version is {supported}"
        )


class MalformedMigrationError(StorageError):
    """Migration metadata or required schema objects are incomplete."""


class CorruptStorageError(StorageError):
    """Persisted data cannot be validated without unsafe repair or inference."""


class PersistedModelVersionError(StorageError):
    """A persisted domain payload uses an unsupported model version."""

    def __init__(self, record_type: str, actual: int, supported: int) -> None:
        self.record_type = record_type
        self.actual = actual
        self.supported = supported
        super().__init__(
            f"unsupported persisted {record_type} model version {actual}; "
            f"supported version is {supported}"
        )


class RunNotFoundError(StorageError):
    """No run exists for the requested canonical identifier."""

    def __init__(self, run_id: RunId) -> None:
        self.run_id = run_id
        super().__init__(f"run {run_id.root} was not found")


class DuplicateRunError(StorageError):
    """A run already exists for the requested canonical identifier."""

    def __init__(self, run_id: RunId) -> None:
        self.run_id = run_id
        super().__init__(f"run {run_id.root} already exists")


class InvalidInitialRunStateError(StorageError):
    """A newly created run did not begin at the canonical CREATED state."""

    def __init__(self, run_id: RunId) -> None:
        self.run_id = run_id
        super().__init__(f"new run {run_id.root} must begin in CREATED state")


class DuplicateEventError(StorageError):
    """An event idempotency identifier is already owned by another event."""

    def __init__(self, event_id: EventId) -> None:
        self.event_id = event_id
        super().__init__(f"event {event_id.root} already exists")


class ConcurrentRunUpdateError(StorageError):
    """The caller's expected run snapshot is no longer current."""

    def __init__(self, run_id: RunId, expected_revision: int, actual_revision: int) -> None:
        self.run_id = run_id
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision
        super().__init__(
            f"run {run_id.root} revision changed; expected {expected_revision}, "
            f"found {actual_revision}"
        )


class StorageOperationError(StorageError):
    """A database operation failed without exposing adapter details or payloads."""

    def __init__(self, operation: str) -> None:
        self.operation = operation
        super().__init__(f"storage operation failed: {operation}")


class TransitionMismatchError(StorageError):
    """A transition result is not canonical for the supplied expected run."""

    def __init__(self, run_id: RunId) -> None:
        self.run_id = run_id
        super().__init__(f"transition snapshot does not match run {run_id.root}")


@dataclass(frozen=True, slots=True)
class SchemaStatus:
    """Validated compatibility information for an initialized database."""

    schema_version: int
    migrations: tuple[str, ...]
    foreign_keys_enabled: bool


@dataclass(frozen=True, slots=True)
class StoredRun:
    """A run plus the optimistic-concurrency revision stored with it."""

    run: Run
    revision: int

    def __post_init__(self) -> None:
        if self.revision < 0:
            raise ValueError("stored run revision cannot be negative")


class RunRepository(Protocol):
    """Minimal application-facing durable run repository."""

    def initialize(self) -> SchemaStatus: ...

    def schema_status(self) -> SchemaStatus: ...

    def create_run(self, run: Run) -> StoredRun: ...

    def get_run(self, run_id: RunId) -> StoredRun: ...

    def run_exists(self, run_id: RunId) -> bool: ...

    def list_events(self, run_id: RunId) -> tuple[RunEvent, ...]: ...

    def persist_transition(
        self,
        expected: StoredRun,
        result: TransitionResult,
        *,
        event_id: EventId,
    ) -> StoredRun: ...
