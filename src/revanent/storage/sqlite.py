"""SQLite adapter for durable, revisioned run state and append-only events."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from pydantic import ValidationError

from revanent.domain import (
    AttemptCounterKind,
    DomainError,
    EventId,
    Run,
    RunEvent,
    RunId,
    RunState,
    TransitionMetadata,
    TransitionResult,
    transition_run,
)
from revanent.ports.orchestration import (
    ORCHESTRATION_SCHEMA_VERSION,
    OrchestrationRecord,
    RecordWriteResult,
)
from revanent.ports.runtime import RuntimeBinding
from revanent.ports.storage import (
    ConcurrentRunUpdateError,
    CorruptStorageError,
    DuplicateEventError,
    DuplicateRunError,
    InvalidInitialRunStateError,
    MalformedMigrationError,
    PersistedModelVersionError,
    RunNotFoundError,
    SchemaStatus,
    StorageError,
    StorageNotInitializedError,
    StorageOperationError,
    StoragePathError,
    StoredRun,
    TransitionMismatchError,
    UnsupportedSchemaVersionError,
)
from revanent.ports.telemetry import (
    BudgetDecision,
    BudgetDecisionStatus,
    BudgetMetric,
    BudgetPolicy,
    BudgetReservation,
    BudgetSettlement,
    ReservationStatus,
    UsageMetric,
    UsageRecord,
)

STORAGE_SCHEMA_VERSION = 5
DOMAIN_MODEL_VERSION = 1


@dataclass(frozen=True, slots=True)
class Migration:
    """One inspectable, ordered, forward-only schema migration."""

    version: int
    name: str
    statements: tuple[str, ...]


_STATE_CHECK = (
    "'CREATED','PLANNING','CONTEXT_PREPARING','WORKSPACE_PREPARING',"
    "'BUILDING','VALIDATING','REVIEWING','REPAIRING','APPROVED','FAILED',"
    "'BLOCKED','CANCELLED'"
)

MIGRATIONS = (
    Migration(
        version=1,
        name="initial_run_state_and_events",
        statements=(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY CHECK (version > 0),
                name TEXT NOT NULL UNIQUE CHECK (length(name) BETWEEN 1 AND 128),
                applied_at TEXT NOT NULL CHECK (substr(applied_at, -1, 1) = 'Z')
            )
            """,
            f"""
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY
                    CHECK (length(run_id) = 36 AND substr(run_id, 1, 4) = 'run_'),
                revision INTEGER NOT NULL CHECK (revision >= 0),
                model_version INTEGER NOT NULL CHECK (model_version > 0),
                current_state TEXT NOT NULL CHECK (current_state IN ({_STATE_CHECK})),
                created_at TEXT NOT NULL CHECK (length(created_at) BETWEEN 20 AND 35),
                updated_at TEXT NOT NULL CHECK (length(updated_at) BETWEEN 20 AND 35),
                run_payload_json TEXT NOT NULL
                    CHECK (length(run_payload_json) BETWEEN 2 AND 1048576)
                    CHECK (json_valid(run_payload_json))
                    CHECK (json_extract(run_payload_json, '$.id') = run_id)
                    CHECK (json_extract(run_payload_json, '$.schema_version') = model_version)
                    CHECK (json_extract(run_payload_json, '$.state') = current_state)
                    CHECK (json_extract(run_payload_json, '$.created_at') = created_at)
                    CHECK (json_extract(run_payload_json, '$.updated_at') = updated_at)
            )
            """,
            f"""
            CREATE TABLE run_events (
                event_id TEXT PRIMARY KEY
                    CHECK (length(event_id) = 38 AND substr(event_id, 1, 6) = 'event_'),
                run_id TEXT NOT NULL,
                sequence INTEGER NOT NULL CHECK (sequence >= 1),
                event_type TEXT NOT NULL CHECK (event_type = 'STATE_TRANSITION'),
                payload_version INTEGER NOT NULL CHECK (payload_version > 0),
                occurred_at TEXT NOT NULL CHECK (length(occurred_at) BETWEEN 20 AND 35),
                source_state TEXT NOT NULL CHECK (source_state IN ({_STATE_CHECK})),
                destination_state TEXT NOT NULL CHECK (destination_state IN ({_STATE_CHECK})),
                reason TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 2048),
                metadata_json TEXT NOT NULL
                    CHECK (length(metadata_json) BETWEEN 2 AND 65536)
                    CHECK (json_valid(metadata_json)),
                event_payload_json TEXT NOT NULL
                    CHECK (length(event_payload_json) BETWEEN 2 AND 262144)
                    CHECK (json_valid(event_payload_json))
                    CHECK (json_extract(event_payload_json, '$.id') = event_id)
                    CHECK (json_extract(event_payload_json, '$.run_id') = run_id)
                    CHECK (json_extract(event_payload_json, '$.sequence') = sequence)
                    CHECK (json_extract(event_payload_json, '$.event_type') = event_type)
                    CHECK (json_extract(event_payload_json, '$.schema_version') = payload_version)
                    CHECK (json_extract(event_payload_json, '$.occurred_at') = occurred_at)
                    CHECK (json_extract(event_payload_json, '$.transition.source') = source_state)
                    CHECK (
                        json_extract(event_payload_json, '$.transition.destination')
                        = destination_state
                    )
                    CHECK (json_extract(event_payload_json, '$.transition.reason') = reason),
                CONSTRAINT uq_run_event_sequence UNIQUE (run_id, sequence),
                CONSTRAINT fk_run_events_run
                    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE RESTRICT
            )
            """,
            """
            CREATE INDEX idx_run_events_run_time
                ON run_events(run_id, occurred_at, sequence)
            """,
            """
            CREATE TRIGGER trg_run_events_no_update
            BEFORE UPDATE ON run_events
            BEGIN
                SELECT RAISE(ABORT, 'run events are append-only');
            END
            """,
            """
            CREATE TRIGGER trg_run_events_no_delete
            BEFORE DELETE ON run_events
            BEGIN
                SELECT RAISE(ABORT, 'run events are append-only');
            END
            """,
        ),
    ),
    Migration(
        version=2,
        name="append_only_orchestration_journal",
        statements=(
            f"""
            CREATE TABLE orchestration_records (
                record_id TEXT PRIMARY KEY
                    CHECK (length(record_id) = 69 AND substr(record_id, 1, 5) = 'orec_'),
                run_id TEXT NOT NULL,
                sequence INTEGER NOT NULL CHECK (sequence BETWEEN 1 AND 1024),
                run_revision INTEGER NOT NULL CHECK (run_revision >= 0),
                expected_state TEXT NOT NULL CHECK (expected_state IN ({_STATE_CHECK})),
                record_stage TEXT NOT NULL
                    CHECK (record_stage IN ('INTENT','OUTCOME','RECONCILIATION')),
                attempt_id TEXT NOT NULL
                    CHECK (length(attempt_id) = 41 AND substr(attempt_id, 1, 9) = 'oattempt_'),
                attempt_kind TEXT NOT NULL
                    CHECK (
                        attempt_kind IN (
                            'CONTEXT','WORKSPACE','BUILD','VALIDATION','REVIEW','REPAIR'
                        )
                    ),
                occurred_at TEXT NOT NULL CHECK (length(occurred_at) BETWEEN 20 AND 35),
                payload_version INTEGER NOT NULL CHECK (payload_version > 0),
                record_payload_json TEXT NOT NULL
                    CHECK (length(record_payload_json) BETWEEN 2 AND 1048576)
                    CHECK (json_valid(record_payload_json))
                    CHECK (json_extract(record_payload_json, '$.id') = record_id)
                    CHECK (json_extract(record_payload_json, '$.run_id') = run_id)
                    CHECK (json_extract(record_payload_json, '$.sequence') = sequence)
                    CHECK (json_extract(record_payload_json, '$.run_revision') = run_revision)
                    CHECK (json_extract(record_payload_json, '$.expected_state') = expected_state)
                    CHECK (json_extract(record_payload_json, '$.stage') = record_stage)
                    CHECK (json_extract(record_payload_json, '$.attempt.attempt_id') = attempt_id)
                    CHECK (json_extract(record_payload_json, '$.attempt.kind') = attempt_kind)
                    CHECK (json_extract(record_payload_json, '$.occurred_at') = occurred_at)
                    CHECK (json_extract(record_payload_json, '$.schema_version') = payload_version),
                CONSTRAINT uq_orchestration_sequence UNIQUE (run_id, sequence),
                CONSTRAINT uq_orchestration_stage UNIQUE (run_id, attempt_id, record_stage),
                CONSTRAINT fk_orchestration_run
                    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE RESTRICT
            )
            """,
            """
            CREATE INDEX idx_orchestration_run_attempt
                ON orchestration_records(run_id, attempt_id, sequence)
            """,
            """
            CREATE TRIGGER trg_orchestration_no_update
            BEFORE UPDATE ON orchestration_records
            BEGIN
                SELECT RAISE(ABORT, 'orchestration records are append-only');
            END
            """,
            """
            CREATE TRIGGER trg_orchestration_no_delete
            BEFORE DELETE ON orchestration_records
            BEGIN
                SELECT RAISE(ABORT, 'orchestration records are append-only');
            END
            """,
        ),
    ),
    Migration(
        version=3,
        name="context_manifest_orchestration_evidence",
        statements=(
            "DROP TRIGGER trg_orchestration_no_update",
            "DROP TRIGGER trg_orchestration_no_delete",
            "DROP INDEX idx_orchestration_run_attempt",
            "ALTER TABLE orchestration_records RENAME TO orchestration_records_v2",
            f"""
            CREATE TABLE orchestration_records (
                record_id TEXT PRIMARY KEY
                    CHECK (length(record_id) = 69 AND substr(record_id, 1, 5) = 'orec_'),
                run_id TEXT NOT NULL,
                sequence INTEGER NOT NULL CHECK (sequence BETWEEN 1 AND 1024),
                run_revision INTEGER NOT NULL CHECK (run_revision >= 0),
                expected_state TEXT NOT NULL CHECK (expected_state IN ({_STATE_CHECK})),
                record_stage TEXT NOT NULL
                    CHECK (record_stage IN ('INTENT','OUTCOME','RECONCILIATION')),
                attempt_id TEXT NOT NULL
                    CHECK (length(attempt_id) = 41 AND substr(attempt_id, 1, 9) = 'oattempt_'),
                attempt_kind TEXT NOT NULL
                    CHECK (
                        attempt_kind IN (
                            'CONTEXT','WORKSPACE','BUILD','VALIDATION','REVIEW','REPAIR'
                        )
                    ),
                occurred_at TEXT NOT NULL CHECK (length(occurred_at) BETWEEN 20 AND 35),
                payload_version INTEGER NOT NULL CHECK (payload_version > 0),
                record_payload_json TEXT NOT NULL
                    CHECK (length(record_payload_json) BETWEEN 2 AND 1048576)
                    CHECK (json_valid(record_payload_json))
                    CHECK (json_extract(record_payload_json, '$.id') = record_id)
                    CHECK (json_extract(record_payload_json, '$.run_id') = run_id)
                    CHECK (json_extract(record_payload_json, '$.sequence') = sequence)
                    CHECK (json_extract(record_payload_json, '$.run_revision') = run_revision)
                    CHECK (json_extract(record_payload_json, '$.expected_state') = expected_state)
                    CHECK (json_extract(record_payload_json, '$.stage') = record_stage)
                    CHECK (json_extract(record_payload_json, '$.attempt.attempt_id') = attempt_id)
                    CHECK (json_extract(record_payload_json, '$.attempt.kind') = attempt_kind)
                    CHECK (json_extract(record_payload_json, '$.occurred_at') = occurred_at)
                    CHECK (json_extract(record_payload_json, '$.schema_version') = payload_version),
                CONSTRAINT uq_orchestration_sequence UNIQUE (run_id, sequence),
                CONSTRAINT uq_orchestration_stage UNIQUE (run_id, attempt_id, record_stage),
                CONSTRAINT fk_orchestration_run
                    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE RESTRICT
            )
            """,
            """
            INSERT INTO orchestration_records (
                record_id, run_id, sequence, run_revision, expected_state, record_stage,
                attempt_id, attempt_kind, occurred_at, payload_version, record_payload_json
            )
            SELECT
                record_id, run_id, sequence, run_revision, expected_state, record_stage,
                attempt_id, attempt_kind, occurred_at, payload_version, record_payload_json
            FROM orchestration_records_v2
            ORDER BY run_id, sequence
            """,
            "DROP TABLE orchestration_records_v2",
            """
            CREATE INDEX idx_orchestration_run_attempt
                ON orchestration_records(run_id, attempt_id, sequence)
            """,
            """
            CREATE TRIGGER trg_orchestration_no_update
            BEFORE UPDATE ON orchestration_records
            BEGIN
                SELECT RAISE(ABORT, 'orchestration records are append-only');
            END
            """,
            """
            CREATE TRIGGER trg_orchestration_no_delete
            BEFORE DELETE ON orchestration_records
            BEGIN
                SELECT RAISE(ABORT, 'orchestration records are append-only');
            END
            """,
        ),
    ),
    Migration(
        version=4,
        name="append_only_usage_and_budget_reservations",
        statements=(
            """
            CREATE TABLE usage_records (
                usage_id TEXT PRIMARY KEY
                    CHECK (length(usage_id) = 70 AND substr(usage_id, 1, 6) = 'usage_'),
                run_id TEXT NOT NULL,
                correlation_key TEXT NOT NULL CHECK (length(correlation_key) BETWEEN 1 AND 128),
                metric TEXT NOT NULL CHECK (length(metric) BETWEEN 1 AND 64),
                provenance TEXT NOT NULL CHECK (length(provenance) BETWEEN 1 AND 32),
                payload_version INTEGER NOT NULL CHECK (payload_version = 1),
                payload_json TEXT NOT NULL CHECK (length(payload_json) BETWEEN 2 AND 65536)
                    CHECK (json_valid(payload_json))
                    CHECK (json_extract(payload_json, '$.id') = usage_id)
                    CHECK (json_extract(payload_json, '$.run_id') = run_id)
                    CHECK (json_extract(payload_json, '$.correlation_key') = correlation_key)
                    CHECK (json_extract(payload_json, '$.metric') = metric)
                    CHECK (json_extract(payload_json, '$.provenance') = provenance),
                CONSTRAINT uq_usage_correlation UNIQUE (run_id, correlation_key, metric),
                CONSTRAINT fk_usage_run FOREIGN KEY (run_id)
                    REFERENCES runs(run_id) ON DELETE RESTRICT
            )
            """,
            """
            CREATE TABLE budget_reservations (
                reservation_id TEXT PRIMARY KEY
                    CHECK (
                        length(reservation_id) = 72
                        AND substr(reservation_id, 1, 8) = 'reserve_'
                    ),
                run_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 128),
                metric TEXT NOT NULL CHECK (length(metric) BETWEEN 1 AND 64),
                payload_version INTEGER NOT NULL CHECK (payload_version = 1),
                payload_json TEXT NOT NULL CHECK (length(payload_json) BETWEEN 2 AND 65536)
                    CHECK (json_valid(payload_json))
                    CHECK (json_extract(payload_json, '$.id') = reservation_id)
                    CHECK (json_extract(payload_json, '$.run_id') = run_id)
                    CHECK (json_extract(payload_json, '$.idempotency_key') = idempotency_key)
                    CHECK (json_extract(payload_json, '$.metric') = metric),
                CONSTRAINT uq_reservation_boundary UNIQUE (run_id, idempotency_key, metric),
                CONSTRAINT fk_reservation_run FOREIGN KEY (run_id)
                    REFERENCES runs(run_id) ON DELETE RESTRICT
            )
            """,
            """
            CREATE TABLE budget_settlements (
                reservation_id TEXT PRIMARY KEY,
                payload_version INTEGER NOT NULL CHECK (payload_version = 1),
                payload_json TEXT NOT NULL CHECK (length(payload_json) BETWEEN 2 AND 65536)
                    CHECK (json_valid(payload_json))
                    CHECK (json_extract(payload_json, '$.reservation_id') = reservation_id),
                CONSTRAINT fk_settlement_reservation
                    FOREIGN KEY (reservation_id) REFERENCES budget_reservations(reservation_id)
                    ON DELETE RESTRICT
            )
            """,
            "CREATE INDEX idx_usage_records_run ON usage_records(run_id, metric, correlation_key)",
            "CREATE INDEX idx_budget_reservations_run ON budget_reservations(run_id, metric)",
            """
            CREATE TRIGGER trg_usage_records_no_update BEFORE UPDATE ON usage_records
            BEGIN SELECT RAISE(ABORT, 'usage records are append-only'); END
            """,
            """
            CREATE TRIGGER trg_usage_records_no_delete BEFORE DELETE ON usage_records
            BEGIN SELECT RAISE(ABORT, 'usage records are append-only'); END
            """,
            """
            CREATE TRIGGER trg_budget_reservations_no_update BEFORE UPDATE ON budget_reservations
            BEGIN SELECT RAISE(ABORT, 'budget reservations are append-only'); END
            """,
            """
            CREATE TRIGGER trg_budget_reservations_no_delete BEFORE DELETE ON budget_reservations
            BEGIN SELECT RAISE(ABORT, 'budget reservations are append-only'); END
            """,
            """
            CREATE TRIGGER trg_budget_settlements_no_update BEFORE UPDATE ON budget_settlements
            BEGIN SELECT RAISE(ABORT, 'budget settlements are append-only'); END
            """,
            """
            CREATE TRIGGER trg_budget_settlements_no_delete BEFORE DELETE ON budget_settlements
            BEGIN SELECT RAISE(ABORT, 'budget settlements are append-only'); END
            """,
        ),
    ),
    Migration(
        version=5,
        name="runtime_repository_bindings",
        statements=(
            """
            CREATE TABLE runtime_bindings (
                run_id TEXT PRIMARY KEY,
                repository_id TEXT NOT NULL
                    CHECK (length(repository_id) = 69 AND substr(repository_id, 1, 5) = 'repo_'),
                worktree_id TEXT NOT NULL UNIQUE
                    CHECK (length(worktree_id) = 35 AND substr(worktree_id, 1, 3) = 'wt_'),
                payload_version INTEGER NOT NULL CHECK (payload_version = 1),
                payload_json TEXT NOT NULL CHECK (length(payload_json) BETWEEN 2 AND 262144)
                    CHECK (json_valid(payload_json))
                    CHECK (json_extract(payload_json, '$.run_id') = run_id)
                    CHECK (json_extract(payload_json, '$.repository.repository_id') = repository_id)
                    CHECK (json_extract(payload_json, '$.worktree_id') = worktree_id)
                    CHECK (json_extract(payload_json, '$.schema_version') = payload_version),
                CONSTRAINT fk_runtime_binding_run FOREIGN KEY (run_id)
                    REFERENCES runs(run_id) ON DELETE RESTRICT
            )
            """,
            """
            CREATE TRIGGER trg_runtime_bindings_no_update BEFORE UPDATE ON runtime_bindings
            BEGIN SELECT RAISE(ABORT, 'runtime bindings are immutable'); END
            """,
            """
            CREATE TRIGGER trg_runtime_bindings_no_delete BEFORE DELETE ON runtime_bindings
            BEGIN SELECT RAISE(ABORT, 'runtime bindings are immutable'); END
            """,
        ),
    ),
)

_REQUIRED_OBJECTS = {
    "schema_migrations": "table",
    "runs": "table",
    "run_events": "table",
    "idx_run_events_run_time": "index",
    "trg_run_events_no_update": "trigger",
    "trg_run_events_no_delete": "trigger",
    "orchestration_records": "table",
    "idx_orchestration_run_attempt": "index",
    "trg_orchestration_no_update": "trigger",
    "trg_orchestration_no_delete": "trigger",
    "usage_records": "table",
    "budget_reservations": "table",
    "budget_settlements": "table",
    "idx_usage_records_run": "index",
    "idx_budget_reservations_run": "index",
    "trg_usage_records_no_update": "trigger",
    "trg_usage_records_no_delete": "trigger",
    "trg_budget_reservations_no_update": "trigger",
    "trg_budget_reservations_no_delete": "trigger",
    "trg_budget_settlements_no_update": "trigger",
    "trg_budget_settlements_no_delete": "trigger",
    "runtime_bindings": "table",
    "trg_runtime_bindings_no_update": "trigger",
    "trg_runtime_bindings_no_delete": "trigger",
}
_REQUIRED_COLUMNS = {
    "schema_migrations": {"version", "name", "applied_at"},
    "runs": {
        "run_id",
        "revision",
        "model_version",
        "current_state",
        "created_at",
        "updated_at",
        "run_payload_json",
    },
    "run_events": {
        "event_id",
        "run_id",
        "sequence",
        "event_type",
        "payload_version",
        "occurred_at",
        "source_state",
        "destination_state",
        "reason",
        "metadata_json",
        "event_payload_json",
    },
    "orchestration_records": {
        "record_id",
        "run_id",
        "sequence",
        "run_revision",
        "expected_state",
        "record_stage",
        "attempt_id",
        "attempt_kind",
        "occurred_at",
        "payload_version",
        "record_payload_json",
    },
    "usage_records": {
        "usage_id",
        "run_id",
        "correlation_key",
        "metric",
        "provenance",
        "payload_version",
        "payload_json",
    },
    "budget_reservations": {
        "reservation_id",
        "run_id",
        "idempotency_key",
        "metric",
        "payload_version",
        "payload_json",
    },
    "budget_settlements": {"reservation_id", "payload_version", "payload_json"},
    "runtime_bindings": {
        "run_id",
        "repository_id",
        "worktree_id",
        "payload_version",
        "payload_json",
    },
}
_TABLE_INFO_QUERIES = {
    "schema_migrations": "PRAGMA table_info(schema_migrations)",
    "runs": "PRAGMA table_info(runs)",
    "run_events": "PRAGMA table_info(run_events)",
    "orchestration_records": "PRAGMA table_info(orchestration_records)",
    "usage_records": "PRAGMA table_info(usage_records)",
    "budget_reservations": "PRAGMA table_info(budget_reservations)",
    "budget_settlements": "PRAGMA table_info(budget_settlements)",
    "runtime_bindings": "PRAGMA table_info(runtime_bindings)",
}


class SQLiteRunRepository:
    """Short-lived-connection SQLite implementation of the run repository port."""

    def __init__(
        self,
        path: Path,
        *,
        timeout_seconds: float = 5.0,
        create_parent: bool = False,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("SQLite timeout must be positive")
        self._path = path
        self._timeout_seconds = timeout_seconds
        self._create_parent = create_parent

    @property
    def path(self) -> Path:
        """Return the configured pathlib database path without creating it."""
        return self._path

    def initialize(self) -> SchemaStatus:
        """Create schema version 1 or validate an existing compatible database."""
        self._prepare_parent()
        if self._path.exists() and not self._path.is_file():
            raise StoragePathError("database path is not a regular file")
        try:
            with (
                self._connect(read_only=False, require_existing=False) as connection,
                self._transaction(connection, write=True),
            ):
                tables = self._user_tables(connection)
                if not tables:
                    self._apply_migrations(connection)
                elif "schema_migrations" not in tables:
                    raise MalformedMigrationError(
                        "existing database has no Revanent migration metadata"
                    )
                else:
                    latest = self._validate_migration_history(connection)[0]
                    self._apply_migrations(connection, after_version=latest)
                return self._assert_current_schema(connection)
        except StorageError:
            raise
        except sqlite3.DatabaseError:
            raise StorageOperationError("initialize database") from None

    def schema_status(self) -> SchemaStatus:
        """Validate compatibility without creating a database or parent directory."""
        try:
            with (
                self._connect(read_only=True, require_existing=True) as connection,
                self._transaction(connection, write=False),
            ):
                return self._assert_current_schema(connection)
        except StorageError:
            raise
        except sqlite3.DatabaseError:
            raise StorageOperationError("read schema status") from None

    def create_run(self, run: Run) -> StoredRun:
        """Insert a new revision-zero run; creation intentionally emits no event."""
        if run.state is not RunState.CREATED:
            raise InvalidInitialRunStateError(run.id)
        payload = run.model_dump_json()
        try:
            with self._connect(read_only=False, require_existing=True) as connection:
                try:
                    with self._transaction(connection, write=True):
                        self._assert_current_schema(connection)
                        connection.execute(
                            """
                            INSERT INTO runs (
                                run_id, revision, model_version, current_state,
                                created_at, updated_at, run_payload_json
                            ) VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                run.id.root,
                                0,
                                run.schema_version,
                                run.state.value,
                                _timestamp(run.created_at),
                                _timestamp(run.updated_at),
                                payload,
                            ),
                        )
                except sqlite3.IntegrityError:
                    with self._transaction(connection, write=False):
                        duplicate = connection.execute(
                            "SELECT 1 FROM runs WHERE run_id = ?", (run.id.root,)
                        ).fetchone()
                    if duplicate is not None:
                        raise DuplicateRunError(run.id) from None
                    raise StorageOperationError("create run") from None
        except StorageError:
            raise
        except sqlite3.DatabaseError:
            raise StorageOperationError("create run") from None
        return StoredRun(run=run, revision=0)

    def create_bound_run(self, run: Run, binding: RuntimeBinding) -> StoredRun:
        """Atomically persist a revision-zero Run and its immutable repository binding."""
        if run.state is not RunState.CREATED:
            raise InvalidInitialRunStateError(run.id)
        if binding.run_id != run.id:
            raise StorageOperationError("runtime binding correlation mismatch")
        try:
            with self._connect(read_only=False, require_existing=True) as connection:
                try:
                    with self._transaction(connection, write=True):
                        self._assert_current_schema(connection)
                        connection.execute(
                            """
                            INSERT INTO runs (
                                run_id, revision, model_version, current_state,
                                created_at, updated_at, run_payload_json
                            ) VALUES (?, 0, ?, ?, ?, ?, ?)
                            """,
                            (
                                run.id.root,
                                run.schema_version,
                                run.state.value,
                                _timestamp(run.created_at),
                                _timestamp(run.updated_at),
                                run.model_dump_json(),
                            ),
                        )
                        connection.execute(
                            """
                            INSERT INTO runtime_bindings (
                                run_id, repository_id, worktree_id, payload_version, payload_json
                            ) VALUES (?, ?, ?, ?, ?)
                            """,
                            (
                                run.id.root,
                                binding.repository.repository_id,
                                binding.worktree_id.root,
                                binding.schema_version,
                                binding.model_dump_json(),
                            ),
                        )
                except sqlite3.IntegrityError:
                    raise StorageOperationError("create bound run") from None
        except StorageError:
            raise
        except sqlite3.DatabaseError:
            raise StorageOperationError("create bound run") from None
        return StoredRun(run=run, revision=0)

    def get_runtime_binding(self, run_id: RunId) -> RuntimeBinding:
        """Load immutable repository identity evidence without changing access state."""
        try:
            with (
                self._connect(read_only=True, require_existing=True) as connection,
                self._transaction(connection, write=False),
            ):
                self._assert_current_schema(connection)
                row = connection.execute(
                    "SELECT payload_json FROM runtime_bindings WHERE run_id = ?",
                    (run_id.root,),
                ).fetchone()
                if row is None:
                    exists = connection.execute(
                        "SELECT 1 FROM runs WHERE run_id = ?", (run_id.root,)
                    ).fetchone()
                    if exists is None:
                        raise RunNotFoundError(run_id)
                    raise CorruptStorageError("run has no runtime repository binding")
                try:
                    binding = RuntimeBinding.model_validate_json(
                        _row_str(row, "payload_json", "runtime binding payload")
                    )
                except ValidationError:
                    raise CorruptStorageError(
                        "runtime repository binding failed validation"
                    ) from None
                if binding.run_id != run_id:
                    raise CorruptStorageError("runtime repository binding correlation mismatch")
                return binding
        except StorageError:
            raise
        except sqlite3.DatabaseError:
            raise StorageOperationError("get runtime binding") from None

    def get_run(self, run_id: RunId) -> StoredRun:
        """Load and fully validate one run from a consistent read transaction."""
        try:
            with (
                self._connect(read_only=True, require_existing=True) as connection,
                self._transaction(connection, write=False),
            ):
                self._assert_current_schema(connection)
                row = connection.execute(
                    "SELECT * FROM runs WHERE run_id = ?", (run_id.root,)
                ).fetchone()
                if row is None:
                    raise RunNotFoundError(run_id)
                return _deserialize_run(row)
        except StorageError:
            raise
        except sqlite3.DatabaseError:
            raise StorageOperationError("get run") from None

    def run_exists(self, run_id: RunId) -> bool:
        """Check existence without creating an absent database."""
        try:
            with (
                self._connect(read_only=True, require_existing=True) as connection,
                self._transaction(connection, write=False),
            ):
                self._assert_current_schema(connection)
                row = connection.execute(
                    "SELECT 1 FROM runs WHERE run_id = ?", (run_id.root,)
                ).fetchone()
                return row is not None
        except StorageError:
            raise
        except sqlite3.DatabaseError:
            raise StorageOperationError("check run existence") from None

    def list_events(self, run_id: RunId) -> tuple[RunEvent, ...]:
        """Return validated events in canonical per-run sequence order."""
        try:
            with (
                self._connect(read_only=True, require_existing=True) as connection,
                self._transaction(connection, write=False),
            ):
                self._assert_current_schema(connection)
                exists = connection.execute(
                    "SELECT 1 FROM runs WHERE run_id = ?", (run_id.root,)
                ).fetchone()
                if exists is None:
                    raise RunNotFoundError(run_id)
                rows = connection.execute(
                    """
                    SELECT * FROM run_events
                    WHERE run_id = ?
                    ORDER BY sequence ASC
                    """,
                    (run_id.root,),
                ).fetchall()
                events = tuple(_deserialize_event(row) for row in rows)
                expected_sequences = tuple(range(1, len(events) + 1))
                if tuple(event.sequence for event in events) != expected_sequences:
                    raise CorruptStorageError("run event sequence is not contiguous")
                return events
        except StorageError:
            raise
        except sqlite3.DatabaseError:
            raise StorageOperationError("list run events") from None

    def list_orchestration_records(self, run_id: RunId) -> tuple[OrchestrationRecord, ...]:
        """Return validated append-only orchestration evidence in sequence order."""
        try:
            with (
                self._connect(read_only=True, require_existing=True) as connection,
                self._transaction(connection, write=False),
            ):
                self._assert_current_schema(connection)
                if (
                    connection.execute(
                        "SELECT 1 FROM runs WHERE run_id = ?", (run_id.root,)
                    ).fetchone()
                    is None
                ):
                    raise RunNotFoundError(run_id)
                rows = connection.execute(
                    """
                    SELECT * FROM orchestration_records
                    WHERE run_id = ? ORDER BY sequence ASC
                    """,
                    (run_id.root,),
                ).fetchall()
                records = tuple(_deserialize_orchestration_record(row) for row in rows)
                if tuple(item.sequence for item in records) != tuple(range(1, len(records) + 1)):
                    raise CorruptStorageError("orchestration record sequence is not contiguous")
                return records
        except StorageError:
            raise
        except sqlite3.DatabaseError:
            raise StorageOperationError("list orchestration records") from None

    def persist_orchestration_record(
        self,
        expected: StoredRun,
        record: OrchestrationRecord,
    ) -> RecordWriteResult:
        """Append intent/outcome evidence only while run revision and state still match."""
        if (
            record.run_id != expected.run.id
            or record.work_package_id != expected.run.work_package.id
            or record.run_revision != expected.revision
            or record.expected_state is not expected.run.state
        ):
            raise TransitionMismatchError(expected.run.id)
        try:
            with self._connect(read_only=False, require_existing=True) as connection:
                try:
                    with self._transaction(connection, write=True):
                        self._assert_current_schema(connection)
                        row = connection.execute(
                            "SELECT * FROM runs WHERE run_id = ?", (expected.run.id.root,)
                        ).fetchone()
                        if row is None:
                            raise RunNotFoundError(expected.run.id)
                        current = _deserialize_run(row)
                        if current != expected:
                            raise ConcurrentRunUpdateError(
                                expected.run.id, expected.revision, current.revision
                            )
                        existing = connection.execute(
                            "SELECT * FROM orchestration_records WHERE record_id = ?",
                            (record.id.root,),
                        ).fetchone()
                        if existing is not None:
                            persisted = _deserialize_orchestration_record(existing)
                            if persisted == record or _same_orchestration_boundary(
                                persisted, record
                            ):
                                return RecordWriteResult(record=persisted, created=False)
                            raise StorageOperationError("orchestration record identifier conflict")
                        next_row = connection.execute(
                            """
                            SELECT COALESCE(MAX(sequence), 0) + 1
                            FROM orchestration_records WHERE run_id = ?
                            """,
                            (record.run_id.root,),
                        ).fetchone()
                        if (
                            next_row is None
                            or _row_int(next_row, 0, "orchestration sequence") != record.sequence
                        ):
                            raise ConcurrentRunUpdateError(
                                expected.run.id, expected.revision, current.revision
                            )
                        _insert_orchestration_record(connection, record)
                        return RecordWriteResult(record=record, created=True)
                except sqlite3.IntegrityError:
                    raise StorageOperationError("persist orchestration record") from None
        except StorageError:
            raise
        except sqlite3.DatabaseError:
            raise StorageOperationError("persist orchestration record") from None

    def list_usage_records(self, run_id: RunId) -> tuple[UsageRecord, ...]:
        try:
            with (
                self._connect(read_only=True, require_existing=True) as connection,
                self._transaction(connection, write=False),
            ):
                self._assert_current_schema(connection)
                rows = connection.execute(
                    "SELECT * FROM usage_records WHERE run_id = ? ORDER BY metric, correlation_key",
                    (run_id.root,),
                ).fetchall()
                return tuple(_deserialize_usage_record(row) for row in rows)
        except StorageError:
            raise
        except sqlite3.DatabaseError:
            raise StorageOperationError("list usage records") from None

    def list_reservations(self, run_id: RunId) -> tuple[BudgetReservation, ...]:
        try:
            with (
                self._connect(read_only=True, require_existing=True) as connection,
                self._transaction(connection, write=False),
            ):
                self._assert_current_schema(connection)
                rows = connection.execute(
                    """
                    SELECT r.*, s.payload_json AS settlement_payload_json
                    FROM budget_reservations AS r
                    LEFT JOIN budget_settlements AS s ON s.reservation_id = r.reservation_id
                    WHERE r.run_id = ? ORDER BY r.metric, r.idempotency_key
                    """,
                    (run_id.root,),
                ).fetchall()
                values = []
                for row in rows:
                    reservation = _deserialize_reservation(row)
                    settlement_payload = row["settlement_payload_json"]
                    if settlement_payload is not None:
                        settlement = _deserialize_settlement(settlement_payload)
                        status = settlement.status
                        reservation = BudgetReservation.model_validate(
                            {**reservation.model_dump(mode="python"), "status": status}
                        )
                    values.append(reservation)
                return tuple(values)
        except StorageError:
            raise
        except sqlite3.DatabaseError:
            raise StorageOperationError("list budget reservations") from None

    def record_usage(self, record: UsageRecord) -> bool:
        return self._append_telemetry(
            table="usage_records",
            identifier_column="usage_id",
            identifier=record.id,
            payload=record,
            values=(
                record.id,
                record.run_id.root,
                record.correlation_key,
                record.metric.value,
                record.provenance.value,
                record.schema_version,
                record.model_dump_json(),
            ),
            statement="""
                INSERT INTO usage_records (usage_id, run_id, correlation_key, metric, provenance,
                    payload_version, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
        )

    def reserve(self, reservation: BudgetReservation) -> bool:
        return self._append_telemetry(
            table="budget_reservations",
            identifier_column="reservation_id",
            identifier=reservation.id,
            payload=reservation,
            values=(
                reservation.id,
                reservation.run_id.root,
                reservation.idempotency_key,
                reservation.metric.value,
                reservation.schema_version,
                reservation.model_dump_json(),
            ),
            statement="""
                INSERT INTO budget_reservations (reservation_id, run_id, idempotency_key, metric,
                    payload_version, payload_json) VALUES (?, ?, ?, ?, ?, ?)
            """,
        )

    def reserve_if_allowed(
        self,
        reservation: BudgetReservation,
        policy: BudgetPolicy,
        *,
        expected_revision: int | None = None,
        require_known: bool = False,
    ) -> BudgetDecision:
        """Atomically evaluate one policy limit and append its reservation."""
        try:
            with (
                self._connect(read_only=False, require_existing=True) as connection,
                self._transaction(connection, write=True),
            ):
                self._assert_current_schema(connection)
                row = connection.execute(
                    "SELECT revision FROM runs WHERE run_id = ?", (reservation.run_id.root,)
                ).fetchone()
                if row is None:
                    raise RunNotFoundError(reservation.run_id)
                if (
                    expected_revision is not None
                    and _row_int(row, "revision", "run revision") != expected_revision
                ):
                    return BudgetDecision(
                        status=BudgetDecisionStatus.DENY_STALE_STATE,
                        metric=reservation.metric,
                        unit=reservation.unit,
                        reason_code="stale_run_revision",
                    )
                existing = connection.execute(
                    "SELECT payload_json FROM budget_reservations WHERE reservation_id = ?",
                    (reservation.id,),
                ).fetchone()
                if existing is not None:
                    if (
                        BudgetReservation.model_validate_json(
                            _row_str(existing, "payload_json", "reservation payload")
                        )
                        == reservation
                    ):
                        return BudgetDecision(
                            status=BudgetDecisionStatus.ALLOW,
                            metric=reservation.metric,
                            unit=reservation.unit,
                        )
                    raise StorageOperationError("telemetry reservation identifier conflict")
                limit = next(
                    (item for item in policy.limits if item.metric is reservation.metric), None
                )
                if limit is None:
                    _insert_reservation(connection, reservation)
                    return BudgetDecision(
                        status=BudgetDecisionStatus.ALLOW,
                        metric=reservation.metric,
                        unit=reservation.unit,
                    )
                if limit.currency != reservation.currency:
                    return BudgetDecision(
                        status=BudgetDecisionStatus.DENY_INVALID_REQUEST,
                        metric=reservation.metric,
                        unit=reservation.unit,
                        reason_code="currency_mismatch",
                    )
                usage_metrics = _USAGE_METRICS_BY_BUDGET[reservation.metric]
                placeholders = ", ".join("?" for _ in usage_metrics)
                unavailable = connection.execute(
                    "SELECT 1 FROM usage_records WHERE run_id = ? "
                    f"AND metric IN ({placeholders}) AND provenance = 'UNAVAILABLE' LIMIT 1",
                    (reservation.run_id.root, *(item.value for item in usage_metrics)),
                ).fetchone()
                unresolved = connection.execute(
                    """SELECT 1 FROM budget_reservations AS r JOIN budget_settlements AS s
                    ON s.reservation_id = r.reservation_id
                    WHERE r.run_id = ? AND r.metric = ?
                    AND json_extract(s.payload_json, '$.status') = 'UNRESOLVED' LIMIT 1""",
                    (reservation.run_id.root, reservation.metric.value),
                ).fetchone()
                if unresolved is not None:
                    return BudgetDecision(
                        status=BudgetDecisionStatus.DENY_UNRESOLVED_RESERVATION,
                        metric=reservation.metric,
                        unit=reservation.unit,
                        reason_code="unresolved_reservation",
                    )
                if require_known and unavailable is not None:
                    return BudgetDecision(
                        status=BudgetDecisionStatus.DENY_USAGE_UNAVAILABLE,
                        metric=reservation.metric,
                        unit=reservation.unit,
                        reason_code="usage_unavailable",
                    )
                if reservation.metric is BudgetMetric.TOTAL_DURATION:
                    overage = connection.execute(
                        """SELECT 1 FROM usage_records WHERE run_id = ?
                        AND metric = 'VALIDATION_DURATION'
                        AND json_extract(payload_json, '$.reason_code') =
                        'validation_duration_overage' LIMIT 1""",
                        (reservation.run_id.root,),
                    ).fetchone()
                    if overage is not None:
                        return BudgetDecision(
                            status=BudgetDecisionStatus.DENY_LIMIT_EXHAUSTED,
                            metric=reservation.metric,
                            unit=reservation.unit,
                            reason_code="validation_duration_overage",
                        )
                if limit.integer_limit is not None:
                    used_row = connection.execute(
                        "SELECT COALESCE(SUM(json_extract(payload_json, '$.integer_value')), 0) "
                        "FROM usage_records WHERE run_id = ? "
                        f"AND metric IN ({placeholders}) AND provenance != 'UNAVAILABLE'",
                        (reservation.run_id.root, *(item.value for item in usage_metrics)),
                    ).fetchone()
                    reserved_row = connection.execute(
                        """SELECT COALESCE(
                        SUM(json_extract(r.payload_json, '$.integer_reserved')), 0
                        )
                        FROM budget_reservations AS r LEFT JOIN budget_settlements AS s
                        ON s.reservation_id = r.reservation_id WHERE r.run_id = ? AND r.metric = ?
                        AND s.reservation_id IS NULL""",
                        (reservation.run_id.root, reservation.metric.value),
                    ).fetchone()
                    remaining = (
                        limit.integer_limit
                        - _row_int(used_row, 0, "usage total")
                        - _row_int(reserved_row, 0, "reservation total")
                    )
                    if (
                        reservation.integer_reserved is None
                        or reservation.integer_reserved > remaining
                    ):
                        return BudgetDecision(
                            status=BudgetDecisionStatus.DENY_LIMIT_EXHAUSTED,
                            metric=reservation.metric,
                            unit=reservation.unit,
                            remaining_integer=max(0, remaining),
                            reason_code="limit_exhausted",
                        )
                    _insert_reservation(connection, reservation)
                    return BudgetDecision(
                        status=BudgetDecisionStatus.ALLOW,
                        metric=reservation.metric,
                        unit=reservation.unit,
                        remaining_integer=remaining,
                    )
                assert limit.decimal_limit is not None
                usage_rows = connection.execute(
                    "SELECT * FROM usage_records WHERE run_id = ? "
                    f"AND metric IN ({placeholders}) AND provenance != 'UNAVAILABLE'",
                    (reservation.run_id.root, *(item.value for item in usage_metrics)),
                ).fetchall()
                reservation_rows = connection.execute(
                    """SELECT r.* FROM budget_reservations AS r
                    LEFT JOIN budget_settlements AS s ON s.reservation_id = r.reservation_id
                    WHERE r.run_id = ? AND r.metric = ? AND s.reservation_id IS NULL""",
                    (reservation.run_id.root, reservation.metric.value),
                ).fetchall()
                consumed = sum(
                    (item.decimal_value or Decimal("0"))
                    for item in (_deserialize_usage_record(row) for row in usage_rows)
                )
                active = sum(
                    (item.decimal_reserved or Decimal("0"))
                    for item in (_deserialize_reservation(row) for row in reservation_rows)
                )
                remaining_decimal = limit.decimal_limit - consumed - active
                if (
                    reservation.decimal_reserved is None
                    or reservation.decimal_reserved > remaining_decimal
                ):
                    return BudgetDecision(
                        status=BudgetDecisionStatus.DENY_LIMIT_EXHAUSTED,
                        metric=reservation.metric,
                        unit=reservation.unit,
                        remaining_decimal=max(Decimal("0"), remaining_decimal),
                        reason_code="limit_exhausted",
                    )
                _insert_reservation(connection, reservation)
                return BudgetDecision(
                    status=BudgetDecisionStatus.ALLOW,
                    metric=reservation.metric,
                    unit=reservation.unit,
                    remaining_decimal=remaining_decimal,
                )
        except StorageError:
            raise
        except sqlite3.DatabaseError:
            raise StorageOperationError("atomically reserve budget") from None

    def settle(self, settlement: BudgetSettlement) -> bool:
        try:
            with (
                self._connect(read_only=False, require_existing=True) as connection,
                self._transaction(connection, write=True),
            ):
                self._assert_current_schema(connection)
                reservation = connection.execute(
                    "SELECT 1 FROM budget_reservations WHERE reservation_id = ?",
                    (settlement.reservation_id,),
                ).fetchone()
                if reservation is None:
                    raise StorageOperationError("settle unknown reservation")
                existing = connection.execute(
                    "SELECT payload_json FROM budget_settlements WHERE reservation_id = ?",
                    (settlement.reservation_id,),
                ).fetchone()
                if existing is not None:
                    if (
                        _deserialize_settlement(
                            _row_str(existing, "payload_json", "settlement payload")
                        )
                        == settlement
                    ):
                        return False
                    raise StorageOperationError("budget settlement identifier conflict")
                connection.execute(
                    "INSERT INTO budget_settlements "
                    "(reservation_id, payload_version, payload_json) VALUES (?, ?, ?)",
                    (
                        settlement.reservation_id,
                        settlement.schema_version,
                        settlement.model_dump_json(),
                    ),
                )
                return True
        except StorageError:
            raise
        except sqlite3.DatabaseError:
            raise StorageOperationError("settle budget reservation") from None

    def settle_reservation(
        self,
        reservation: BudgetReservation,
        settlement: BudgetSettlement,
        usage_records: tuple[UsageRecord, ...],
    ) -> bool:
        """Atomically append normalized usage and settle its exact reservation boundary."""
        if settlement.reservation_id != reservation.id:
            raise StorageOperationError("settlement reservation correlation mismatch")
        if settlement.settled_at < reservation.created_at:
            raise StorageOperationError("settlement timestamp precedes reservation")
        if reservation.integer_reserved is not None:
            if settlement.status is ReservationStatus.SETTLED and (
                settlement.integer_consumed is None
                or settlement.decimal_consumed is not None
                or settlement.currency is not None
            ):
                raise StorageOperationError("integer settlement unit mismatch")
        elif settlement.status is ReservationStatus.SETTLED and (
            settlement.decimal_consumed is None
            or settlement.integer_consumed is not None
            or settlement.currency != reservation.currency
        ):
            raise StorageOperationError("Decimal settlement unit mismatch")
        if any(
            item.run_id != reservation.run_id
            or item.work_package_id != reservation.work_package_id
            or item.attempt_id != reservation.attempt_id
            or item.invocation_id != reservation.invocation_id
            for item in usage_records
        ):
            raise StorageOperationError("settlement usage correlation mismatch")
        try:
            with (
                self._connect(read_only=False, require_existing=True) as connection,
                self._transaction(connection, write=True),
            ):
                self._assert_current_schema(connection)
                persisted_row = connection.execute(
                    "SELECT * FROM budget_reservations WHERE reservation_id = ?", (reservation.id,)
                ).fetchone()
                if persisted_row is None:
                    raise StorageOperationError("settle unknown reservation")
                if _deserialize_reservation(persisted_row) != reservation:
                    raise StorageOperationError("settlement reservation payload conflict")
                existing = connection.execute(
                    "SELECT payload_json FROM budget_settlements WHERE reservation_id = ?",
                    (reservation.id,),
                ).fetchone()
                if existing is not None:
                    if (
                        _deserialize_settlement(
                            _row_str(existing, "payload_json", "settlement payload")
                        )
                        != settlement
                    ):
                        raise StorageOperationError("budget settlement identifier conflict")
                    for record in usage_records:
                        row = connection.execute(
                            "SELECT payload_json FROM usage_records WHERE usage_id = ?",
                            (record.id,),
                        ).fetchone()
                        if (
                            row is None
                            or _row_str(row, "payload_json", "usage payload")
                            != record.model_dump_json()
                        ):
                            raise StorageOperationError("settlement usage retry conflict")
                    return False
                for record in usage_records:
                    existing_usage = connection.execute(
                        "SELECT payload_json FROM usage_records WHERE usage_id = ?", (record.id,)
                    ).fetchone()
                    if existing_usage is not None:
                        if (
                            _row_str(existing_usage, "payload_json", "usage payload")
                            != record.model_dump_json()
                        ):
                            raise StorageOperationError("usage identifier conflict")
                        continue
                    connection.execute(
                        """INSERT INTO usage_records (usage_id, run_id, correlation_key, metric,
                        provenance, payload_version, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            record.id,
                            record.run_id.root,
                            record.correlation_key,
                            record.metric.value,
                            record.provenance.value,
                            record.schema_version,
                            record.model_dump_json(),
                        ),
                    )
                connection.execute(
                    "INSERT INTO budget_settlements "
                    "(reservation_id, payload_version, payload_json) VALUES (?, ?, ?)",
                    (reservation.id, settlement.schema_version, settlement.model_dump_json()),
                )
                return True
        except StorageError:
            raise
        except sqlite3.DatabaseError:
            raise StorageOperationError("atomically settle reservation") from None

    def _append_telemetry(
        self,
        *,
        table: str,
        identifier_column: str,
        identifier: str,
        payload: UsageRecord | BudgetReservation,
        values: tuple[object, ...],
        statement: str,
    ) -> bool:
        try:
            with (
                self._connect(read_only=False, require_existing=True) as connection,
                self._transaction(connection, write=True),
            ):
                self._assert_current_schema(connection)
                existing = connection.execute(
                    f"SELECT payload_json FROM {table} WHERE {identifier_column} = ?",
                    (identifier,),
                ).fetchone()
                if existing is not None:
                    text = _row_str(existing, "payload_json", "telemetry payload")
                    if text == payload.model_dump_json():
                        return False
                    raise StorageOperationError("telemetry identifier conflict")
                connection.execute(statement, values)
                return True
        except StorageError:
            raise
        except sqlite3.DatabaseError:
            raise StorageOperationError("append telemetry") from None

    def persist_transition(
        self,
        expected: StoredRun,
        result: TransitionResult,
        *,
        event_id: EventId,
    ) -> StoredRun:
        """Atomically compare/update run state and append exactly one transition event."""
        _validate_transition_snapshot(expected, result)
        try:
            with self._connect(read_only=False, require_existing=True) as connection:
                try:
                    with self._transaction(connection, write=True):
                        self._assert_current_schema(connection)
                        row = connection.execute(
                            "SELECT * FROM runs WHERE run_id = ?",
                            (expected.run.id.root,),
                        ).fetchone()
                        if row is None:
                            raise RunNotFoundError(expected.run.id)
                        current = _deserialize_run(row)
                        if current.revision != expected.revision:
                            replay = connection.execute(
                                "SELECT * FROM run_events WHERE event_id = ?",
                                (event_id.root,),
                            ).fetchone()
                            if (
                                current.revision == expected.revision + 1
                                and current.run == result.run
                                and replay is not None
                                and _deserialize_event(replay).transition == result.transition
                            ):
                                return current
                            if replay is not None:
                                raise DuplicateEventError(event_id)
                            raise ConcurrentRunUpdateError(
                                expected.run.id, expected.revision, current.revision
                            )
                        if current.run != expected.run:
                            raise ConcurrentRunUpdateError(
                                expected.run.id, expected.revision, current.revision
                            )

                        next_revision = expected.revision + 1
                        updated = connection.execute(
                            """
                            UPDATE runs
                            SET revision = ?, model_version = ?, current_state = ?,
                                updated_at = ?, run_payload_json = ?
                            WHERE run_id = ? AND revision = ? AND current_state = ?
                            """,
                            (
                                next_revision,
                                result.run.schema_version,
                                result.run.state.value,
                                _timestamp(result.run.updated_at),
                                result.run.model_dump_json(),
                                expected.run.id.root,
                                expected.revision,
                                expected.run.state.value,
                            ),
                        )
                        if updated.rowcount != 1:
                            raise ConcurrentRunUpdateError(
                                expected.run.id, expected.revision, current.revision
                            )

                        sequence_row = connection.execute(
                            """
                            SELECT COALESCE(MAX(sequence), 0) + 1
                            FROM run_events WHERE run_id = ?
                            """,
                            (expected.run.id.root,),
                        ).fetchone()
                        if sequence_row is None:
                            raise CorruptStorageError("cannot allocate run event sequence")
                        sequence = _row_int(sequence_row, 0, "event sequence")
                        event = RunEvent(
                            id=event_id,
                            run_id=result.run.id,
                            sequence=sequence,
                            occurred_at=result.transition.occurred_at,
                            transition=result.transition,
                        )
                        _insert_event(connection, event)
                        return StoredRun(run=result.run, revision=next_revision)
                except sqlite3.IntegrityError:
                    with self._transaction(connection, write=False):
                        duplicate = connection.execute(
                            "SELECT 1 FROM run_events WHERE event_id = ?", (event_id.root,)
                        ).fetchone()
                    if duplicate is not None:
                        raise DuplicateEventError(event_id) from None
                    raise StorageOperationError("persist transition event") from None
        except StorageError:
            raise
        except sqlite3.DatabaseError:
            raise StorageOperationError("persist transition") from None

    def _prepare_parent(self) -> None:
        parent = self._path.parent
        if parent.exists():
            if not parent.is_dir():
                raise StoragePathError("database parent is not a directory")
            return
        if not self._create_parent:
            raise StoragePathError("database parent does not exist")
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise StoragePathError("database parent could not be created") from None

    @contextmanager
    def _connect(self, *, read_only: bool, require_existing: bool) -> Iterator[sqlite3.Connection]:
        if require_existing and not self._path.is_file():
            raise StorageNotInitializedError("storage database is not initialized")
        database = self._path.resolve().as_uri() + "?mode=ro" if read_only else str(self._path)
        try:
            connection = sqlite3.connect(
                database,
                timeout=self._timeout_seconds,
                isolation_level=None,
                uri=read_only,
            )
        except sqlite3.DatabaseError:
            raise StorageOperationError("open database") from None
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            enabled_row = connection.execute("PRAGMA foreign_keys").fetchone()
            if enabled_row is None or _row_int(enabled_row, 0, "foreign-key status") != 1:
                raise StorageOperationError("enable foreign keys")
            yield connection
        finally:
            connection.close()

    @staticmethod
    @contextmanager
    def _transaction(connection: sqlite3.Connection, *, write: bool) -> Iterator[None]:
        connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        try:
            yield
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        else:
            connection.commit()

    @staticmethod
    def _user_tables(connection: sqlite3.Connection) -> set[str]:
        rows = connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            """
        ).fetchall()
        return {_row_str(row, 0, "schema object name") for row in rows}

    @staticmethod
    def _apply_migrations(connection: sqlite3.Connection, *, after_version: int = 0) -> None:
        for migration in MIGRATIONS:
            if migration.version <= after_version:
                continue
            for statement in migration.statements:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                (migration.version, migration.name, _timestamp(datetime.now(UTC))),
            )

    @staticmethod
    def _validate_migration_history(
        connection: sqlite3.Connection,
    ) -> tuple[int, tuple[str, ...]]:
        try:
            rows = connection.execute(
                "SELECT version, name, applied_at FROM schema_migrations ORDER BY version"
            ).fetchall()
        except sqlite3.DatabaseError:
            raise MalformedMigrationError("migration metadata cannot be read") from None
        if not rows:
            raise MalformedMigrationError("migration history is empty")

        versions = tuple(_row_int(row, "version", "migration version") for row in rows)
        latest = max(versions)
        if latest > STORAGE_SCHEMA_VERSION:
            raise UnsupportedSchemaVersionError(latest, STORAGE_SCHEMA_VERSION)
        expected_prefix = MIGRATIONS[: len(rows)]
        if versions != tuple(migration.version for migration in expected_prefix):
            raise MalformedMigrationError("migration versions are incomplete or out of order")

        names = tuple(_row_str(row, "name", "migration name") for row in rows)
        if names != tuple(migration.name for migration in expected_prefix):
            raise MalformedMigrationError("migration names do not match the supported history")
        for row in rows:
            _parse_persisted_timestamp(_row_str(row, "applied_at", "migration timestamp"))
        return latest, names

    @staticmethod
    def _assert_current_schema(connection: sqlite3.Connection) -> SchemaStatus:
        latest, names = SQLiteRunRepository._validate_migration_history(connection)
        if latest != STORAGE_SCHEMA_VERSION or len(names) != len(MIGRATIONS):
            raise MalformedMigrationError("database is not at the complete current schema")

        object_rows = connection.execute(
            "SELECT name, type FROM sqlite_master WHERE name IN ("
            + ", ".join("?" for _ in _REQUIRED_OBJECTS)
            + ")",
            tuple(_REQUIRED_OBJECTS),
        ).fetchall()
        objects = {
            _row_str(row, "name", "schema object name"): _row_str(row, "type", "schema object type")
            for row in object_rows
        }
        if objects != _REQUIRED_OBJECTS:
            raise MalformedMigrationError("required schema objects are missing or malformed")
        for table, expected_columns in _REQUIRED_COLUMNS.items():
            column_rows = connection.execute(_TABLE_INFO_QUERIES[table]).fetchall()
            actual_columns = {_row_str(row, "name", "schema column name") for row in column_rows}
            if actual_columns != expected_columns:
                raise MalformedMigrationError(
                    f"{table} columns do not match schema version {STORAGE_SCHEMA_VERSION}"
                )

        integrity = connection.execute("PRAGMA quick_check").fetchone()
        if integrity is None or _row_str(integrity, 0, "integrity status") != "ok":
            raise CorruptStorageError("SQLite integrity check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise CorruptStorageError("SQLite foreign-key check failed")
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()
        enabled = foreign_keys is not None and _row_int(foreign_keys, 0, "foreign-key status") == 1
        if not enabled:
            raise StorageOperationError("verify foreign keys")
        return SchemaStatus(
            schema_version=STORAGE_SCHEMA_VERSION,
            migrations=names,
            foreign_keys_enabled=True,
        )


def _validate_transition_snapshot(expected: StoredRun, result: TransitionResult) -> None:
    if expected.run.id != result.run.id or result.transition.run_id != expected.run.id:
        raise TransitionMismatchError(expected.run.id)
    metadata = {item.key: item.value for item in result.transition.metadata}
    deltas = {
        kind: getattr(result.run.attempts, kind.value) - getattr(expected.run.attempts, kind.value)
        for kind in AttemptCounterKind
    }
    incremented = tuple(kind for kind, delta in deltas.items() if delta == 1)
    if any(delta not in {0, 1} for delta in deltas.values()) or len(incremented) > 1:
        raise TransitionMismatchError(expected.run.id)
    try:
        canonical = transition_run(
            expected.run,
            result.transition.destination,
            occurred_at=result.transition.occurred_at,
            reason=result.transition.reason,
            metadata=metadata,
            approval_gate=result.run.approval_gate,
            increment_attempt=incremented[0] if incremented else None,
        )
    except (DomainError, ValueError):
        raise TransitionMismatchError(expected.run.id) from None
    if canonical != result:
        raise TransitionMismatchError(expected.run.id)


def _insert_event(connection: sqlite3.Connection, event: RunEvent) -> None:
    transition = event.transition
    connection.execute(
        """
        INSERT INTO run_events (
            event_id, run_id, sequence, event_type, payload_version,
            occurred_at, source_state, destination_state, reason,
            metadata_json, event_payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.id.root,
            event.run_id.root,
            event.sequence,
            event.event_type.value,
            event.schema_version,
            _timestamp(event.occurred_at),
            transition.source.value,
            transition.destination.value,
            transition.reason,
            _metadata_json(transition.metadata),
            event.model_dump_json(),
        ),
    )


def _insert_orchestration_record(
    connection: sqlite3.Connection, record: OrchestrationRecord
) -> None:
    connection.execute(
        """
        INSERT INTO orchestration_records (
            record_id, run_id, sequence, run_revision, expected_state,
            record_stage, attempt_id, attempt_kind, occurred_at,
            payload_version, record_payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.id.root,
            record.run_id.root,
            record.sequence,
            record.run_revision,
            record.expected_state.value,
            record.stage.value,
            record.attempt.attempt_id.root,
            record.attempt.kind.value,
            _timestamp(record.occurred_at),
            record.schema_version,
            record.model_dump_json(),
        ),
    )


def _same_orchestration_boundary(
    persisted: OrchestrationRecord, candidate: OrchestrationRecord
) -> bool:
    """Compare stable boundary evidence while ignoring coordinator-local clock values."""
    left = persisted.model_dump(mode="python")
    right = candidate.model_dump(mode="python")
    for value in (left, right):
        value.pop("sequence")
        value.pop("occurred_at")
        attempt = value["attempt"]
        attempt.pop("started_at")
        attempt.pop("completed_at")
        reconciliation = value.get("reconciliation")
        if reconciliation is not None:
            reconciliation.pop("observed_at")
    return left == right


def _deserialize_run(row: sqlite3.Row) -> StoredRun:
    model_version = _row_int(row, "model_version", "run model version")
    if model_version != DOMAIN_MODEL_VERSION:
        raise PersistedModelVersionError("run", model_version, DOMAIN_MODEL_VERSION)
    payload = _row_str(row, "run_payload_json", "run payload")
    try:
        run = Run.model_validate_json(payload)
    except ValidationError:
        raise CorruptStorageError("run payload failed domain validation") from None
    if (
        run.schema_version != model_version
        or run.id.root != _row_str(row, "run_id", "run identifier")
        or run.state.value != _row_str(row, "current_state", "run state")
        or _timestamp(run.created_at) != _row_str(row, "created_at", "run created timestamp")
        or _timestamp(run.updated_at) != _row_str(row, "updated_at", "run updated timestamp")
    ):
        raise CorruptStorageError("run normalized fields do not match its payload")
    revision = _row_int(row, "revision", "run revision")
    try:
        return StoredRun(run=run, revision=revision)
    except ValueError:
        raise CorruptStorageError("run revision is invalid") from None


def _deserialize_event(row: sqlite3.Row) -> RunEvent:
    payload_version = _row_int(row, "payload_version", "event payload version")
    if payload_version != DOMAIN_MODEL_VERSION:
        raise PersistedModelVersionError("event", payload_version, DOMAIN_MODEL_VERSION)
    payload = _row_str(row, "event_payload_json", "event payload")
    try:
        event = RunEvent.model_validate_json(payload)
    except ValidationError:
        raise CorruptStorageError("event payload failed domain validation") from None

    metadata_text = _row_str(row, "metadata_json", "event metadata")
    try:
        metadata_document: object = json.loads(metadata_text)
    except (json.JSONDecodeError, TypeError):
        raise CorruptStorageError("event metadata is not valid JSON") from None
    expected_metadata = [item.model_dump(mode="json") for item in event.transition.metadata]
    transition = event.transition
    if (
        event.schema_version != payload_version
        or event.id.root != _row_str(row, "event_id", "event identifier")
        or event.run_id.root != _row_str(row, "run_id", "event run identifier")
        or event.sequence != _row_int(row, "sequence", "event sequence")
        or event.event_type.value != _row_str(row, "event_type", "event type")
        or _timestamp(event.occurred_at) != _row_str(row, "occurred_at", "event timestamp")
        or transition.source.value != _row_str(row, "source_state", "event source state")
        or transition.destination.value
        != _row_str(row, "destination_state", "event destination state")
        or transition.reason != _row_str(row, "reason", "event reason")
        or metadata_document != expected_metadata
    ):
        raise CorruptStorageError("event normalized fields do not match its payload")
    return event


def _deserialize_orchestration_record(row: sqlite3.Row) -> OrchestrationRecord:
    payload_version = _row_int(row, "payload_version", "orchestration payload version")
    if payload_version != ORCHESTRATION_SCHEMA_VERSION:
        raise PersistedModelVersionError(
            "orchestration record", payload_version, ORCHESTRATION_SCHEMA_VERSION
        )
    try:
        record = OrchestrationRecord.model_validate_json(
            _row_str(row, "record_payload_json", "orchestration record payload")
        )
    except ValidationError:
        raise CorruptStorageError(
            "orchestration record payload failed contract validation"
        ) from None
    if (
        record.schema_version != payload_version
        or record.id.root != _row_str(row, "record_id", "orchestration record identifier")
        or record.run_id.root != _row_str(row, "run_id", "orchestration run identifier")
        or record.sequence != _row_int(row, "sequence", "orchestration sequence")
        or record.run_revision != _row_int(row, "run_revision", "orchestration run revision")
        or record.expected_state.value
        != _row_str(row, "expected_state", "orchestration expected state")
        or record.stage.value != _row_str(row, "record_stage", "orchestration stage")
        or record.attempt.attempt_id.root
        != _row_str(row, "attempt_id", "orchestration attempt identifier")
        or record.attempt.kind.value != _row_str(row, "attempt_kind", "orchestration attempt kind")
        or _timestamp(record.occurred_at) != _row_str(row, "occurred_at", "orchestration timestamp")
    ):
        raise CorruptStorageError("orchestration normalized fields do not match its payload")
    return record


def _deserialize_usage_record(row: sqlite3.Row) -> UsageRecord:
    try:
        value = UsageRecord.model_validate_json(_row_str(row, "payload_json", "usage payload"))
    except ValidationError:
        raise CorruptStorageError("usage payload failed telemetry validation") from None
    if (
        value.id != _row_str(row, "usage_id", "usage identifier")
        or value.run_id.root != _row_str(row, "run_id", "usage run identifier")
        or value.correlation_key != _row_str(row, "correlation_key", "usage correlation")
        or value.metric.value != _row_str(row, "metric", "usage metric")
        or value.provenance.value != _row_str(row, "provenance", "usage provenance")
        or value.schema_version != _row_int(row, "payload_version", "usage payload version")
    ):
        raise CorruptStorageError("usage normalized fields do not match its payload")
    return value


def _deserialize_reservation(row: sqlite3.Row) -> BudgetReservation:
    try:
        value = BudgetReservation.model_validate_json(
            _row_str(row, "payload_json", "reservation payload")
        )
    except ValidationError:
        raise CorruptStorageError("reservation payload failed telemetry validation") from None
    if (
        value.id != _row_str(row, "reservation_id", "reservation identifier")
        or value.run_id.root != _row_str(row, "run_id", "reservation run identifier")
        or value.idempotency_key != _row_str(row, "idempotency_key", "reservation idempotency")
        or value.metric.value != _row_str(row, "metric", "reservation metric")
        or value.schema_version != _row_int(row, "payload_version", "reservation payload version")
    ):
        raise CorruptStorageError("reservation normalized fields do not match its payload")
    return value


def _deserialize_settlement(payload: str) -> BudgetSettlement:
    try:
        return BudgetSettlement.model_validate_json(payload)
    except ValidationError:
        raise CorruptStorageError("settlement payload failed telemetry validation") from None


_USAGE_METRICS_BY_BUDGET = {
    BudgetMetric.BUILD_ATTEMPTS: (UsageMetric.BUILD_ATTEMPTS,),
    BudgetMetric.REVIEW_ATTEMPTS: (UsageMetric.REVIEW_ATTEMPTS,),
    BudgetMetric.REPAIR_ATTEMPTS: (UsageMetric.REPAIR_ATTEMPTS,),
    BudgetMetric.TOTAL_DURATION: (
        UsageMetric.COMMAND_DURATION,
        UsageMetric.VALIDATION_DURATION,
        UsageMetric.PROVIDER_DURATION,
    ),
    BudgetMetric.REMOTE_TOKENS: (UsageMetric.TOTAL_TOKENS,),
    BudgetMetric.ESTIMATED_COST: (UsageMetric.ESTIMATED_COST,),
}


def _insert_reservation(connection: sqlite3.Connection, reservation: BudgetReservation) -> None:
    connection.execute(
        """INSERT INTO budget_reservations (reservation_id, run_id, idempotency_key, metric,
        payload_version, payload_json) VALUES (?, ?, ?, ?, ?, ?)""",
        (
            reservation.id,
            reservation.run_id.root,
            reservation.idempotency_key,
            reservation.metric.value,
            reservation.schema_version,
            reservation.model_dump_json(),
        ),
    )


def _metadata_json(metadata: tuple[TransitionMetadata, ...]) -> str:
    document = [item.model_dump(mode="json") for item in metadata]
    return json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _parse_persisted_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise MalformedMigrationError("migration timestamp is invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise MalformedMigrationError("migration timestamp is not UTC")
    return parsed


def _row_str(row: sqlite3.Row, key: str | int, label: str) -> str:
    value = row[key]
    if not isinstance(value, str):
        raise CorruptStorageError(f"{label} has an invalid storage type")
    return value


def _row_int(row: sqlite3.Row, key: str | int, label: str) -> int:
    value = row[key]
    if type(value) is not int:
        raise CorruptStorageError(f"{label} has an invalid storage type")
    return value
