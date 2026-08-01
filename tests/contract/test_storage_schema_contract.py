from __future__ import annotations

import sqlite3
from pathlib import Path

from revanent.storage import MIGRATIONS, SQLiteRunRepository


def test_migration_history_is_inspectable_ordered_and_versioned() -> None:
    assert [(migration.version, migration.name) for migration in MIGRATIONS] == [
        (1, "initial_run_state_and_events"),
        (2, "append_only_orchestration_journal"),
        (3, "context_manifest_orchestration_evidence"),
        (4, "append_only_usage_and_budget_reservations"),
        (5, "runtime_repository_bindings"),
    ]
    assert all(migration.statements for migration in MIGRATIONS)


def test_schema_version_5_contract_has_required_objects_and_constraints(tmp_path: Path) -> None:
    path = tmp_path / "contract.db"
    SQLiteRunRepository(path).initialize()

    with sqlite3.connect(path) as connection:
        objects = dict(
            connection.execute(
                """
                SELECT name, type FROM sqlite_master
                WHERE name IN (
                    'schema_migrations', 'runs', 'run_events',
                    'idx_run_events_run_time',
                    'trg_run_events_no_update', 'trg_run_events_no_delete',
                    'orchestration_records', 'idx_orchestration_run_attempt',
                    'trg_orchestration_no_update', 'trg_orchestration_no_delete',
                    'usage_records', 'budget_reservations', 'budget_settlements',
                    'idx_usage_records_run', 'idx_budget_reservations_run',
                    'runtime_bindings', 'trg_runtime_bindings_no_update',
                    'trg_runtime_bindings_no_delete'
                )
                """
            ).fetchall()
        )
        run_columns = {row[1] for row in connection.execute("PRAGMA table_info(runs)").fetchall()}
        event_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(run_events)").fetchall()
        }
        foreign_keys = connection.execute("PRAGMA foreign_key_list(run_events)").fetchall()
        orchestration_foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(orchestration_records)"
        ).fetchall()
        binding_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(runtime_bindings)").fetchall()
        }
        binding_foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(runtime_bindings)"
        ).fetchall()
        run_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'runs'"
        ).fetchone()
        orchestration_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'orchestration_records'"
        ).fetchone()

    assert objects == {
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
        "runtime_bindings": "table",
        "trg_runtime_bindings_no_update": "trigger",
        "trg_runtime_bindings_no_delete": "trigger",
    }
    assert run_columns == {
        "run_id",
        "revision",
        "model_version",
        "current_state",
        "created_at",
        "updated_at",
        "run_payload_json",
    }
    assert event_columns == {
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
    }
    assert len(foreign_keys) == 1
    assert foreign_keys[0][2:5] == ("runs", "run_id", "run_id")
    assert len(orchestration_foreign_keys) == 1
    assert orchestration_foreign_keys[0][2:5] == ("runs", "run_id", "run_id")
    assert binding_columns == {
        "run_id",
        "repository_id",
        "worktree_id",
        "payload_version",
        "payload_json",
    }
    assert len(binding_foreign_keys) == 1
    assert binding_foreign_keys[0][2:5] == ("runs", "run_id", "run_id")
    assert run_sql is not None
    assert "json_valid(run_payload_json)" in run_sql[0]
    assert orchestration_sql is not None
    assert "'CONTEXT'" in orchestration_sql[0]
