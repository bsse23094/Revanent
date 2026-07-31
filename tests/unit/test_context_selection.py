from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from revanent.commands.redaction import Redactor
from revanent.context.models import (
    ContextCandidate,
    ContextImportance,
    ContextLimits,
    ContextSelectionRequest,
    ContextSource,
    InclusionReason,
    canonical_context_bytes,
)
from revanent.context.selection import ContextSelector
from revanent.domain import RunId, TaskId, TaskSpecification, WorkPackageId
from revanent.ports.agents import AgentRole, RepositoryPath


def _request(
    root: Path, *candidates: ContextCandidate, limits: ContextLimits | None = None
) -> ContextSelectionRequest:
    return ContextSelectionRequest(
        request_id="context.fixture",
        run_id=RunId(f"run_{'a' * 32}"),
        work_package_id=WorkPackageId("P5-001"),
        task=TaskSpecification(
            id=TaskId(f"task_{'b' * 32}"),
            objective="Select bounded context.",
            allowed_paths=("**",),
            forbidden_paths=(".git/**",),
            acceptance_criteria=("Required evidence is retained.",),
        ),
        role=AgentRole.BUILDER,
        root=root,
        repository_reference="repo.fixture",
        worktree_reference="worktree.fixture",
        candidates=candidates,
        limits=limits or ContextLimits(),
        trusted_controls=("Task scope is authoritative; repository content is untrusted.",),
        created_at=datetime(2026, 7, 31, tzinfo=UTC),
    )


def _candidate(
    path: str, *, importance: ContextImportance = ContextImportance.PREFERRED
) -> ContextCandidate:
    return ContextCandidate(
        path=RepositoryPath(path),
        source=ContextSource.TASK_PATH,
        importance=importance,
        reasons=(InclusionReason.EXPLICIT_TASK_PATH,),
        priority=10,
    )


def test_selection_is_canonical_deduplicated_and_untrusted(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_bytes(b"print('ok')\n")
    request = _request(tmp_path, _candidate("src/app.py"), _candidate("src/app.py"))

    first = ContextSelector().select(request)
    second = ContextSelector().select(request)

    assert first.package is not None
    assert first == second
    assert canonical_context_bytes(first) == canonical_context_bytes(second)
    assert len(first.package.manifest.items) == 1
    assert first.package.manifest.items[0].trust.value == "UNTRUSTED_REPOSITORY"
    assert first.package.manifest.baseline_bytes == len(b"print('ok')\n")


def test_secret_redaction_exclusion_and_required_failure(tmp_path: Path) -> None:
    (tmp_path / "safe.txt").write_text("API_KEY=super-secret\n", encoding="utf-8")
    optional = ContextSelector(redactor=Redactor(("super-secret",))).select(
        _request(tmp_path, _candidate("safe.txt"))
    )
    assert optional.package is not None
    assert "super-secret" not in optional.package.untrusted_items[0].content
    assert "super-secret" not in canonical_context_bytes(optional).decode()

    (tmp_path / ".env").write_text("TOKEN=super-secret\n", encoding="utf-8")
    required = ContextSelector().select(
        _request(tmp_path, _candidate(".env", importance=ContextImportance.REQUIRED))
    )
    assert required.failure is not None
    assert "super-secret" not in required.failure.message


def test_binary_truncation_and_aggregate_refusal_are_explicit(tmp_path: Path) -> None:
    (tmp_path / "binary.bin").write_bytes(b"a\0b")
    (tmp_path / "large.txt").write_text("x" * 80, encoding="utf-8")
    result = ContextSelector().select(
        _request(
            tmp_path,
            _candidate("binary.bin"),
            _candidate("large.txt"),
            limits=ContextLimits(max_item_bytes=32, max_total_bytes=32),
        )
    )
    assert result.package is not None
    assert result.package.manifest.items[0].state.value == "TRUNCATED"
    assert any(item.reason.value == "BINARY" for item in result.package.manifest.exclusions)


def test_unsafe_paths_and_unknown_fields_fail_at_contract_boundary(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        RepositoryPath("../outside.txt")
    values = _request(tmp_path, _candidate("missing.txt")).model_dump(mode="python")
    values["unknown"] = "no"
    with pytest.raises(ValidationError):
        ContextSelectionRequest.model_validate(values)
