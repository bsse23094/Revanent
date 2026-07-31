from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from revanent.context import (
    ContextDiscoveryInput,
    ContextSelectionRequest,
    ContextSelector,
    ExclusionReason,
)
from revanent.domain import RunId, TaskId, TaskSpecification, WorkPackageId
from revanent.ports import AgentRole, RepositoryPath


def test_real_symlink_or_windows_junction_escape_is_rejected(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    outside = tmp_path / "repository-sibling"
    (repository / "src").mkdir(parents=True)
    outside.mkdir()
    (outside / "secret.txt").write_text("outside\n", encoding="utf-8")
    link = repository / "src" / "linked"
    if os.name == "nt":
        from tests.unit.test_command_policy import _create_windows_junction

        _create_windows_junction(link, outside)
    else:
        link.symlink_to(outside, target_is_directory=True)

    request = ContextSelectionRequest(
        request_id="context.escape",
        run_id=RunId(f"run_{'a' * 32}"),
        work_package_id=WorkPackageId("P5-001"),
        task=TaskSpecification(
            id=TaskId(f"task_{'b' * 32}"),
            objective="Reject linked escapes.",
            allowed_paths=("src/**",),
            forbidden_paths=(".git/**",),
            acceptance_criteria=("No sibling reads.",),
        ),
        role=AgentRole.BUILDER,
        root=repository.resolve(),
        repository_reference="repo.fixture",
        worktree_reference="worktree.fixture",
        discovery=ContextDiscoveryInput(explicit_paths=(RepositoryPath("src/linked/secret.txt"),)),
        trusted_controls=("Linked paths cannot expand scope.",),
        created_at=datetime(2026, 7, 31, tzinfo=UTC),
    )

    result = ContextSelector().select(request)

    assert result.failure is not None
    assert result.failure.category is ExclusionReason.SYMLINK_ESCAPE
    assert "outside" not in result.failure.message
