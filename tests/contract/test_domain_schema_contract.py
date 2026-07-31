from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from revanent.domain import (
    BudgetLimits,
    Run,
    RunId,
    RunState,
    TaskId,
    TaskSpecification,
    WorkPackage,
    WorkPackageId,
)


def _run() -> Run:
    now = datetime(2026, 7, 30, 12, tzinfo=UTC)
    return Run(
        id=RunId("run_0123456789abcdef0123456789abcdef"),
        task=TaskSpecification(
            id=TaskId("task_0123456789abcdef0123456789abcdef"),
            objective="Freeze the serialized contract.",
            allowed_paths=("src/**",),
            acceptance_criteria=("Contract remains versioned.",),
        ),
        work_package=WorkPackage(
            id=WorkPackageId("P1-001"),
            title="Domain schema contract",
            objective="Freeze version 1.",
        ),
        budgets=BudgetLimits(
            max_duration_seconds=60,
            max_build_attempts=1,
            max_review_attempts=1,
            max_repair_attempts=0,
        ),
        created_at=now,
        updated_at=now,
    )


def test_run_version_1_json_shape_uses_strings_not_raw_model_dictionaries() -> None:
    document = json.loads(_run().model_dump_json())

    assert document["schema_version"] == 1
    assert document["id"] == "run_0123456789abcdef0123456789abcdef"
    assert document["task"]["id"] == "task_0123456789abcdef0123456789abcdef"
    assert document["work_package"]["id"] == "P1-001"
    assert document["state"] == "CREATED"
    assert document["approval_gate"] is None


def test_domain_schema_rejects_unknown_versions_and_extra_fields() -> None:
    document = _run().model_dump(mode="python")
    document["schema_version"] = 2
    with pytest.raises(ValidationError):
        Run.model_validate(document)

    document = _run().model_dump(mode="python")
    document["unexpected"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Run.model_validate(document)


def test_every_run_state_has_the_canonical_uppercase_wire_value() -> None:
    assert [state.value for state in RunState] == [
        "CREATED",
        "PLANNING",
        "CONTEXT_PREPARING",
        "WORKSPACE_PREPARING",
        "BUILDING",
        "VALIDATING",
        "REVIEWING",
        "REPAIRING",
        "APPROVED",
        "FAILED",
        "BLOCKED",
        "CANCELLED",
    ]
