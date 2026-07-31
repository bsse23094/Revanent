from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from revanent.domain import (
    ApprovalGate,
    AttemptCounters,
    BudgetLimits,
    EventId,
    ReviewResult,
    ReviewVerdict,
    Run,
    RunEvent,
    RunId,
    RunState,
    TaskId,
    TaskSpecification,
    TransitionResult,
    WorkPackage,
    WorkPackageId,
    WorkPackageStatus,
    transition_run,
)
from revanent.ports.storage import (
    ConcurrentRunUpdateError,
    CorruptStorageError,
    DuplicateEventError,
    DuplicateRunError,
    InvalidInitialRunStateError,
    MalformedMigrationError,
    PersistedModelVersionError,
    RunNotFoundError,
    StorageNotInitializedError,
    StoragePathError,
    StoredRun,
    TransitionMismatchError,
    UnsupportedSchemaVersionError,
)
from revanent.storage import SQLiteRunRepository

NOW = datetime(2026, 7, 30, 12, 0, 0, 123456, tzinfo=UTC)


def _run(seed: str = "0", *, state: RunState = RunState.CREATED) -> Run:
    return Run(
        id=RunId(f"run_{seed * 32}"),
        task=TaskSpecification(
            id=TaskId(f"task_{seed * 32}"),
            objective=f"Persist complete run {seed} without inference.",
            allowed_paths=("src/**", "tests/**"),
            forbidden_paths=(".env", ".git/**"),
            acceptance_criteria=("Every field round-trips.", "Events remain ordered."),
        ),
        work_package=WorkPackage(
            id=WorkPackageId("P1-002"),
            title="Durable Run State and Events",
            objective="Provide restart-safe storage primitives.",
            status=WorkPackageStatus.IN_PROGRESS,
        ),
        budgets=BudgetLimits(
            max_duration_seconds=5_400,
            max_build_attempts=3,
            max_review_attempts=4,
            max_repair_attempts=2,
            max_remote_tokens=123_456,
            max_estimated_cost_usd=Decimal("19.95"),
        ),
        attempts=AttemptCounters(build=1, review=1, repair=0),
        state=state,
        created_at=NOW,
        updated_at=NOW,
    )


def _event_id(seed: str) -> EventId:
    return EventId(f"event_{seed * 32}")


def _passing_gate() -> ApprovalGate:
    return ApprovalGate(
        review=ReviewResult(
            verdict=ReviewVerdict.APPROVED,
            summary="All persisted approval gates passed.",
        ),
        required_validation_passed=True,
        review_schema_parsed=True,
        scope_justified=True,
        generated_files_consistent=True,
        evidence_complete=True,
        unexplained_dirty_state=False,
    )


def _result(
    stored: StoredRun,
    destination: RunState,
    *,
    occurred_at: datetime | None = None,
    reason: str = "Persist accepted transition.",
    metadata: dict[str, str] | None = None,
) -> TransitionResult:
    return transition_run(
        stored.run,
        destination,
        occurred_at=occurred_at or stored.run.updated_at + timedelta(seconds=1),
        reason=reason,
        metadata=metadata,
        approval_gate=_passing_gate() if destination is RunState.APPROVED else None,
    )


def _raw_connect(path: Path, *, ignore_checks: bool = False) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    if ignore_checks:
        connection.execute("PRAGMA ignore_check_constraints = ON")
    return connection


def _advance_to_reviewing(repository: SQLiteRunRepository, stored: StoredRun) -> StoredRun:
    destinations = (
        RunState.PLANNING,
        RunState.CONTEXT_PREPARING,
        RunState.WORKSPACE_PREPARING,
        RunState.BUILDING,
        RunState.VALIDATING,
        RunState.REVIEWING,
    )
    for seed, destination in zip("123456", destinations, strict=True):
        stored = repository.persist_transition(
            stored,
            _result(stored, destination),
            event_id=_event_id(seed),
        )
    return stored


def test_fresh_and_repeated_initialization_records_schema_version_once(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    repository = SQLiteRunRepository(path)

    first = repository.initialize()
    with _raw_connect(path) as connection:
        first_history = connection.execute(
            "SELECT version, name, applied_at FROM schema_migrations"
        ).fetchall()
    second = repository.initialize()
    with _raw_connect(path) as connection:
        second_history = connection.execute(
            "SELECT version, name, applied_at FROM schema_migrations"
        ).fetchall()

    assert path.is_file()
    assert first == second
    assert first.schema_version == 4
    assert first.migrations == (
        "initial_run_state_and_events",
        "append_only_orchestration_journal",
        "context_manifest_orchestration_evidence",
        "append_only_usage_and_budget_reservations",
    )
    assert first.foreign_keys_enabled is True
    assert [tuple(row) for row in first_history] == [tuple(row) for row in second_history]


def test_version_1_database_migrates_forward_without_changing_existing_runs(
    tmp_path: Path,
) -> None:
    path = tmp_path / "version-1.db"
    repository = SQLiteRunRepository(path)
    repository.initialize()
    run = _run()
    repository.create_run(run)
    with _raw_connect(path) as connection:
        for trigger in (
            "trg_usage_records_no_delete",
            "trg_usage_records_no_update",
            "trg_budget_reservations_no_delete",
            "trg_budget_reservations_no_update",
            "trg_budget_settlements_no_delete",
            "trg_budget_settlements_no_update",
        ):
            connection.execute(f"DROP TRIGGER {trigger}")
        connection.execute("DROP INDEX idx_usage_records_run")
        connection.execute("DROP INDEX idx_budget_reservations_run")
        connection.execute("DROP TABLE budget_settlements")
        connection.execute("DROP TABLE budget_reservations")
        connection.execute("DROP TABLE usage_records")
        connection.execute("DROP TRIGGER trg_orchestration_no_delete")
        connection.execute("DROP TRIGGER trg_orchestration_no_update")
        connection.execute("DROP INDEX idx_orchestration_run_attempt")
        connection.execute("DROP TABLE orchestration_records")
        connection.execute("DELETE FROM schema_migrations WHERE version >= 2")

    status = SQLiteRunRepository(path).initialize()

    assert status.schema_version == 4
    assert status.migrations[-1] == "append_only_usage_and_budget_reservations"
    assert SQLiteRunRepository(path).get_run(run.id) == StoredRun(run=run, revision=0)


def test_newer_schema_version_is_rejected_without_modification(tmp_path: Path) -> None:
    path = tmp_path / "newer.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations(version INTEGER, name TEXT, applied_at TEXT)"
        )
        connection.execute(
            "INSERT INTO schema_migrations VALUES (?, ?, ?)",
            (5, "future", "2026-07-30T12:00:00Z"),
        )

    with pytest.raises(UnsupportedSchemaVersionError) as captured:
        SQLiteRunRepository(path).initialize()

    assert captured.value.actual == 5
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone() == (1,)


@pytest.mark.parametrize("malformation", ["empty", "wrong_name", "invalid_timestamp"])
def test_malformed_migration_metadata_is_rejected(tmp_path: Path, malformation: str) -> None:
    path = tmp_path / f"malformed-{malformation}.db"
    repository = SQLiteRunRepository(path)
    repository.initialize()
    with _raw_connect(path) as connection:
        if malformation == "empty":
            connection.execute("DELETE FROM schema_migrations")
        elif malformation == "wrong_name":
            connection.execute("UPDATE schema_migrations SET name = 'wrong' WHERE version = 1")
        else:
            connection.execute("UPDATE schema_migrations SET applied_at = 'not-a-timeZ'")

    with pytest.raises(MalformedMigrationError):
        repository.initialize()


def test_partially_applied_or_unowned_database_is_rejected(tmp_path: Path) -> None:
    partial = tmp_path / "partial.db"
    repository = SQLiteRunRepository(partial)
    repository.initialize()
    with _raw_connect(partial) as connection:
        connection.execute("DROP TABLE run_events")
    with pytest.raises(MalformedMigrationError, match="required schema objects"):
        repository.initialize()

    unowned = tmp_path / "unowned.db"
    with sqlite3.connect(unowned) as connection:
        connection.execute("CREATE TABLE unrelated(value TEXT)")
    with pytest.raises(MalformedMigrationError, match="no Revanent migration metadata"):
        SQLiteRunRepository(unowned).initialize()


def test_read_checks_do_not_create_database_and_parent_creation_is_explicit(
    tmp_path: Path,
) -> None:
    path = tmp_path / "missing" / "state.db"
    repository = SQLiteRunRepository(path)

    with pytest.raises(StorageNotInitializedError):
        repository.run_exists(_run().id)
    assert not path.exists()
    assert not path.parent.exists()

    with pytest.raises(StoragePathError, match="parent does not exist"):
        repository.initialize()
    SQLiteRunRepository(path, create_parent=True).initialize()
    assert path.is_file()


def test_database_path_with_spaces_round_trips_on_windows_compatible_path(tmp_path: Path) -> None:
    path = tmp_path / "directory with spaces" / "run state.db"
    repository = SQLiteRunRepository(path, create_parent=True)
    repository.initialize()
    run = _run()

    repository.create_run(run)

    assert repository.get_run(run.id) == StoredRun(run=run, revision=0)


def test_run_creation_retrieval_duplicate_missing_and_multiple_independent_runs(
    tmp_path: Path,
) -> None:
    repository = SQLiteRunRepository(tmp_path / "runs.db")
    repository.initialize()
    first = _run("0")
    second = _run("a")

    assert repository.run_exists(first.id) is False
    assert repository.create_run(first) == StoredRun(run=first, revision=0)
    assert repository.create_run(second) == StoredRun(run=second, revision=0)
    assert repository.run_exists(first.id) is True
    assert repository.get_run(first.id).run == first
    assert repository.get_run(second.id).run == second
    assert repository.list_events(first.id) == ()

    with pytest.raises(DuplicateRunError):
        repository.create_run(first)
    missing = RunId("run_ffffffffffffffffffffffffffffffff")
    with pytest.raises(RunNotFoundError):
        repository.get_run(missing)
    with pytest.raises(RunNotFoundError):
        repository.list_events(missing)

    with pytest.raises(InvalidInitialRunStateError):
        repository.create_run(_run("b", state=RunState.PLANNING))


def test_every_run_field_and_utc_precision_survives_close_and_reopen(tmp_path: Path) -> None:
    path = tmp_path / "reload.db"
    original = _run()
    SQLiteRunRepository(path).initialize()
    SQLiteRunRepository(path).create_run(original)

    reloaded = SQLiteRunRepository(path).get_run(original.id)

    assert reloaded == StoredRun(run=original, revision=0)
    assert reloaded.run.model_dump_json() == original.model_dump_json()
    assert reloaded.run.created_at == NOW
    assert reloaded.run.created_at.tzinfo is not None
    assert reloaded.run.created_at.utcoffset() == timedelta(0)
    assert reloaded.run.budgets.max_estimated_cost_usd == Decimal("19.95")


def test_successful_transition_atomically_updates_run_and_appends_exactly_one_event(
    tmp_path: Path,
) -> None:
    repository = SQLiteRunRepository(tmp_path / "atomic.db")
    repository.initialize()
    stored = repository.create_run(_run())
    result = _result(stored, RunState.PLANNING, metadata={"actor": "orchestrator"})

    updated = repository.persist_transition(stored, result, event_id=_event_id("1"))
    events = repository.list_events(stored.run.id)

    assert updated == StoredRun(run=result.run, revision=1)
    assert repository.get_run(stored.run.id) == updated
    assert len(events) == 1
    assert events[0].sequence == 1
    assert events[0].id == _event_id("1")
    assert events[0].transition == result.transition
    assert events[0].transition.metadata[0].key == "actor"


def test_equal_timestamp_events_reload_in_sequence_order(tmp_path: Path) -> None:
    path = tmp_path / "ordering.db"
    repository = SQLiteRunRepository(path)
    repository.initialize()
    stored = repository.create_run(_run())
    same_time = NOW + timedelta(seconds=1)
    planning = _result(stored, RunState.PLANNING, occurred_at=same_time)
    stored = repository.persist_transition(stored, planning, event_id=_event_id("1"))
    context = _result(stored, RunState.CONTEXT_PREPARING, occurred_at=same_time)
    repository.persist_transition(stored, context, event_id=_event_id("2"))

    events = SQLiteRunRepository(path).list_events(stored.run.id)

    assert [event.sequence for event in events] == [1, 2]
    assert [event.id for event in events] == [_event_id("1"), _event_id("2")]
    assert events[0].occurred_at == events[1].occurred_at == same_time


def test_approved_run_metadata_and_approval_evidence_round_trip(tmp_path: Path) -> None:
    repository = SQLiteRunRepository(tmp_path / "approval.db")
    repository.initialize()
    stored = _advance_to_reviewing(repository, repository.create_run(_run()))
    result = _result(
        stored,
        RunState.APPROVED,
        metadata={"validation_plan": "required", "review": "structured"},
    )

    updated = repository.persist_transition(stored, result, event_id=_event_id("a"))
    event = repository.list_events(stored.run.id)[-1]

    assert updated.run.approval_gate == _passing_gate()
    assert SQLiteRunRepository(repository.path).get_run(stored.run.id) == updated
    assert {item.key: item.value for item in event.transition.metadata} == {
        "review": "structured",
        "validation_plan": "required",
    }


def test_events_are_append_only_in_schema_and_absent_from_public_mutation_api(
    tmp_path: Path,
) -> None:
    path = tmp_path / "append-only.db"
    repository = SQLiteRunRepository(path)
    repository.initialize()
    stored = repository.create_run(_run())
    repository.persist_transition(
        stored, _result(stored, RunState.PLANNING), event_id=_event_id("1")
    )

    assert not hasattr(repository, "update_event")
    assert not hasattr(repository, "delete_event")
    with _raw_connect(path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("UPDATE run_events SET reason = 'changed'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM run_events")


def test_failed_revision_guard_writes_zero_events_and_connection_remains_usable(
    tmp_path: Path,
) -> None:
    repository = SQLiteRunRepository(tmp_path / "guard.db")
    repository.initialize()
    created = repository.create_run(_run())
    incorrect = StoredRun(run=created.run, revision=1)

    with pytest.raises(ConcurrentRunUpdateError):
        repository.persist_transition(
            incorrect,
            _result(incorrect, RunState.PLANNING),
            event_id=_event_id("1"),
        )

    assert repository.get_run(created.run.id) == created
    assert repository.list_events(created.run.id) == ()
    successful = repository.persist_transition(
        created, _result(created, RunState.PLANNING), event_id=_event_id("2")
    )
    assert successful.revision == 1


def test_event_constraint_failure_rolls_back_state_update(tmp_path: Path) -> None:
    repository = SQLiteRunRepository(tmp_path / "event-rollback.db")
    repository.initialize()
    first = repository.create_run(_run("0"))
    second = repository.create_run(_run("a"))
    duplicate_event_id = _event_id("1")
    repository.persist_transition(
        first, _result(first, RunState.PLANNING), event_id=duplicate_event_id
    )

    with pytest.raises(DuplicateEventError):
        repository.persist_transition(
            second,
            _result(second, RunState.PLANNING),
            event_id=duplicate_event_id,
        )

    assert repository.get_run(second.run.id) == second
    assert repository.list_events(second.run.id) == ()


def test_stale_concurrent_snapshot_is_rejected_without_second_event(tmp_path: Path) -> None:
    repository = SQLiteRunRepository(tmp_path / "concurrency.db")
    repository.initialize()
    original = repository.create_run(_run())
    first_snapshot = repository.get_run(original.run.id)
    second_snapshot = repository.get_run(original.run.id)
    repository.persist_transition(
        first_snapshot,
        _result(first_snapshot, RunState.PLANNING),
        event_id=_event_id("1"),
    )

    with pytest.raises(ConcurrentRunUpdateError) as captured:
        repository.persist_transition(
            second_snapshot,
            _result(second_snapshot, RunState.BLOCKED),
            event_id=_event_id("2"),
        )

    assert captured.value.expected_revision == 0
    assert captured.value.actual_revision == 1
    assert len(repository.list_events(original.run.id)) == 1


def test_same_event_id_is_an_idempotent_retry_of_the_same_transition(tmp_path: Path) -> None:
    repository = SQLiteRunRepository(tmp_path / "idempotency.db")
    repository.initialize()
    original = repository.create_run(_run())
    result = _result(original, RunState.PLANNING)
    event_id = _event_id("1")

    first = repository.persist_transition(original, result, event_id=event_id)
    replay = repository.persist_transition(original, result, event_id=event_id)

    assert replay == first
    assert len(repository.list_events(original.run.id)) == 1


def test_noncanonical_transition_snapshot_is_rejected_before_writing(tmp_path: Path) -> None:
    repository = SQLiteRunRepository(tmp_path / "mismatch.db")
    repository.initialize()
    stored = repository.create_run(_run())
    canonical = _result(stored, RunState.PLANNING)
    changed_data = canonical.run.model_dump(mode="python")
    changed_task = canonical.run.task.model_dump(mode="python")
    changed_task["objective"] = "Illegitimately changed immutable task content."
    changed_data["task"] = changed_task
    mismatched = TransitionResult(
        run=Run.model_validate(changed_data),
        transition=canonical.transition,
    )

    with pytest.raises(TransitionMismatchError):
        repository.persist_transition(stored, mismatched, event_id=_event_id("1"))

    assert repository.get_run(stored.run.id) == stored
    assert repository.list_events(stored.run.id) == ()


def test_missing_run_transition_fails_explicitly(tmp_path: Path) -> None:
    repository = SQLiteRunRepository(tmp_path / "missing-transition.db")
    repository.initialize()
    expected = StoredRun(run=_run(), revision=0)

    with pytest.raises(RunNotFoundError):
        repository.persist_transition(
            expected,
            _result(expected, RunState.PLANNING),
            event_id=_event_id("1"),
        )


def test_malformed_run_json_is_rejected_without_echoing_payload(tmp_path: Path) -> None:
    path = tmp_path / "corrupt-json.db"
    repository = SQLiteRunRepository(path)
    repository.initialize()
    run = _run()
    repository.create_run(run)
    secret_marker = "super-secret-value"
    with _raw_connect(path, ignore_checks=True) as connection:
        connection.execute(
            "UPDATE runs SET run_payload_json = ? WHERE run_id = ?",
            (f"{{broken-{secret_marker}", run.id.root),
        )

    with pytest.raises(CorruptStorageError) as captured:
        repository.get_run(run.id)

    assert secret_marker not in str(captured.value)


@pytest.mark.parametrize(
    "corruption", ["invalid_state", "invalid_timestamp", "missing_required_field"]
)
def test_invalid_persisted_run_values_are_rejected(tmp_path: Path, corruption: str) -> None:
    path = tmp_path / f"corrupt-{corruption}.db"
    repository = SQLiteRunRepository(path)
    repository.initialize()
    run = _run()
    repository.create_run(run)
    document = json.loads(run.model_dump_json())
    with _raw_connect(path, ignore_checks=True) as connection:
        if corruption == "invalid_state":
            document["state"] = "NOT_A_STATE"
            connection.execute(
                """
                UPDATE runs SET current_state = ?, run_payload_json = ?
                WHERE run_id = ?
                """,
                ("NOT_A_STATE", json.dumps(document), run.id.root),
            )
        elif corruption == "invalid_timestamp":
            document["updated_at"] = "2026-07-30T12:00:00"
            connection.execute(
                """
                UPDATE runs SET updated_at = ?, run_payload_json = ?
                WHERE run_id = ?
                """,
                ("2026-07-30T12:00:00", json.dumps(document), run.id.root),
            )
        else:
            del document["task"]
            connection.execute(
                "UPDATE runs SET run_payload_json = ? WHERE run_id = ?",
                (json.dumps(document), run.id.root),
            )

    with pytest.raises(CorruptStorageError):
        repository.get_run(run.id)


def test_unknown_persisted_run_model_version_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "future-run.db"
    repository = SQLiteRunRepository(path)
    repository.initialize()
    run = _run()
    repository.create_run(run)
    with _raw_connect(path, ignore_checks=True) as connection:
        connection.execute("UPDATE runs SET model_version = 2 WHERE run_id = ?", (run.id.root,))

    with pytest.raises(PersistedModelVersionError) as captured:
        repository.get_run(run.id)

    assert captured.value.record_type == "run"
    assert captured.value.actual == 2


def _insert_cloned_event(
    path: Path,
    *,
    event_id: EventId,
    payload_version: int = 1,
    destination: str | None = None,
    malformed_payload: bool = False,
) -> None:
    with _raw_connect(
        path, ignore_checks=destination is not None or malformed_payload
    ) as connection:
        row = connection.execute("SELECT * FROM run_events ORDER BY sequence LIMIT 1").fetchone()
        assert row is not None
        payload: dict[str, Any] = json.loads(row["event_payload_json"])
        payload["id"] = event_id.root
        payload["sequence"] = 2
        payload["schema_version"] = payload_version
        normalized_destination = row["destination_state"]
        if destination is not None:
            payload["transition"]["destination"] = destination
            normalized_destination = destination
        payload_text = "{malformed-super-secret-event" if malformed_payload else json.dumps(payload)
        connection.execute(
            """
            INSERT INTO run_events (
                event_id, run_id, sequence, event_type, payload_version,
                occurred_at, source_state, destination_state, reason,
                metadata_json, event_payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id.root,
                row["run_id"],
                2,
                row["event_type"],
                payload_version,
                row["occurred_at"],
                row["source_state"],
                normalized_destination,
                row["reason"],
                row["metadata_json"],
                payload_text,
            ),
        )


def test_unknown_event_payload_version_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "future-event.db"
    repository = SQLiteRunRepository(path)
    repository.initialize()
    stored = repository.create_run(_run())
    repository.persist_transition(
        stored, _result(stored, RunState.PLANNING), event_id=_event_id("1")
    )
    _insert_cloned_event(path, event_id=_event_id("2"), payload_version=2)

    with pytest.raises(PersistedModelVersionError) as captured:
        repository.list_events(stored.run.id)

    assert captured.value.record_type == "event"


def test_invalid_persisted_event_state_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "invalid-event.db"
    repository = SQLiteRunRepository(path)
    repository.initialize()
    stored = repository.create_run(_run())
    repository.persist_transition(
        stored, _result(stored, RunState.PLANNING), event_id=_event_id("1")
    )
    _insert_cloned_event(path, event_id=_event_id("2"), destination="NOT_A_STATE")

    with pytest.raises(CorruptStorageError):
        repository.list_events(stored.run.id)


def test_malformed_event_json_is_rejected_without_echoing_payload(tmp_path: Path) -> None:
    path = tmp_path / "malformed-event.db"
    repository = SQLiteRunRepository(path)
    repository.initialize()
    stored = repository.create_run(_run())
    repository.persist_transition(
        stored, _result(stored, RunState.PLANNING), event_id=_event_id("1")
    )
    _insert_cloned_event(path, event_id=_event_id("2"), malformed_payload=True)

    with pytest.raises(CorruptStorageError) as captured:
        repository.list_events(stored.run.id)

    assert "super-secret-event" not in str(captured.value)


def test_foreign_keys_are_enabled_and_schema_rejects_orphans(tmp_path: Path) -> None:
    path = tmp_path / "foreign-keys.db"
    repository = SQLiteRunRepository(path)
    status = repository.initialize()

    assert status.foreign_keys_enabled is True
    missing_run = _run()
    transition = transition_run(
        missing_run,
        RunState.PLANNING,
        occurred_at=NOW + timedelta(seconds=1),
        reason="Orphan must fail.",
    ).transition
    event = RunEvent(
        id=_event_id("f"),
        run_id=missing_run.id,
        sequence=1,
        occurred_at=transition.occurred_at,
        transition=transition,
    )
    with _raw_connect(path) as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        foreign_keys = connection.execute("PRAGMA foreign_key_list(run_events)").fetchall()
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
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
                    "2026-07-30T12:00:01.123456Z",
                    transition.source.value,
                    transition.destination.value,
                    transition.reason,
                    "[]",
                    event.model_dump_json(),
                ),
            )
    assert len(foreign_keys) == 1
    assert foreign_keys[0][2:5] == ("runs", "run_id", "run_id")
