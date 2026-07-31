from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from revanent.domain import (
    BudgetLimits,
    EventId,
    Run,
    RunEvent,
    RunId,
    RunState,
    TaskId,
    TaskSpecification,
    WorkPackage,
    WorkPackageId,
    transition_run,
)
from revanent.ports.storage import RunRepository, StoredRun
from revanent.storage import SQLiteRunRepository

NOW = datetime(2026, 7, 30, 12, tzinfo=UTC)


def _run() -> Run:
    return Run(
        id=RunId("run_0123456789abcdef0123456789abcdef"),
        task=TaskSpecification(
            id=TaskId("task_0123456789abcdef0123456789abcdef"),
            objective="Persist one run.",
            allowed_paths=("src/**",),
            acceptance_criteria=("State reloads.",),
        ),
        work_package=WorkPackage(
            id=WorkPackageId("P1-002"),
            title="Durable state",
            objective="Persist run state.",
        ),
        budgets=BudgetLimits(
            max_duration_seconds=60,
            max_build_attempts=1,
            max_review_attempts=1,
            max_repair_attempts=0,
        ),
        created_at=NOW,
        updated_at=NOW,
    )


def test_sqlite_adapter_satisfies_storage_port_statically(tmp_path: Path) -> None:
    repository: RunRepository = SQLiteRunRepository(tmp_path / "port.db")

    assert repository is not None


def test_stored_run_rejects_negative_revision() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        StoredRun(run=_run(), revision=-1)


def test_event_id_creation_and_run_event_round_trip() -> None:
    event_id = EventId.new()
    transition = transition_run(
        _run(),
        RunState.PLANNING,
        occurred_at=NOW,
        reason="Start planning.",
    ).transition
    event = RunEvent(
        id=event_id,
        run_id=transition.run_id,
        sequence=1,
        occurred_at=transition.occurred_at,
        transition=transition,
    )

    assert event_id.root.startswith("event_")
    assert RunEvent.model_validate_json(event.model_dump_json()) == event


def test_run_event_rejects_mismatched_run_and_timestamp() -> None:
    transition = transition_run(
        _run(),
        RunState.PLANNING,
        occurred_at=NOW,
        reason="Start planning.",
    ).transition
    with pytest.raises(ValidationError, match="run identifier does not match"):
        RunEvent(
            id=EventId("event_0123456789abcdef0123456789abcdef"),
            run_id=RunId("run_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
            sequence=1,
            occurred_at=NOW,
            transition=transition,
        )
