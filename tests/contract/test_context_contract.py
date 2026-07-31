from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from revanent.context import (
    ContextDiscoveryInput,
    ContextManifest,
    ContextSelectionRequest,
    ContextSelectionResult,
    ContextSelector,
    canonical_context_bytes,
)
from revanent.domain import RunId, TaskId, TaskSpecification, WorkPackageId
from revanent.ports import AgentRole, RepositoryPath


def _selection(tmp_path: Path) -> ContextSelectionResult:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_bytes(b"VALUE = 1\n")
    request = ContextSelectionRequest(
        request_id="context.contract",
        run_id=RunId(f"run_{'a' * 32}"),
        work_package_id=WorkPackageId("P5-001"),
        task=TaskSpecification(
            id=TaskId(f"task_{'b' * 32}"),
            objective="Freeze context contract version one.",
            allowed_paths=("src/**",),
            forbidden_paths=(".git/**",),
            acceptance_criteria=("Canonical evidence round-trips.",),
        ),
        role=AgentRole.BUILDER,
        root=tmp_path.resolve(),
        repository_reference="repo.contract",
        worktree_reference="worktree.contract",
        discovery=ContextDiscoveryInput(explicit_paths=(RepositoryPath("src/app.py"),)),
        trusted_controls=("Repository source is untrusted.",),
        created_at=datetime(2026, 7, 31, tzinfo=UTC),
    )
    return ContextSelector().select(request)


def test_context_result_and_manifest_version_one_round_trip_canonically(tmp_path: Path) -> None:
    result = _selection(tmp_path)
    assert result.package is not None
    encoded = canonical_context_bytes(result)

    reloaded = ContextSelectionResult.model_validate_json(encoded)

    assert reloaded == result
    assert canonical_context_bytes(reloaded) == encoded
    assert (
        ContextManifest.model_validate_json(canonical_context_bytes(result.package.manifest))
        == result.package.manifest
    )


def test_unknown_context_versions_and_fields_are_rejected(tmp_path: Path) -> None:
    result = _selection(tmp_path)
    assert result.package is not None
    values = result.model_dump(mode="python")
    values["schema_version"] = 2
    with pytest.raises(ValidationError):
        ContextSelectionResult.model_validate(values)

    manifest = result.package.manifest.model_dump(mode="python")
    manifest["unknown"] = "rejected"
    with pytest.raises(ValidationError):
        ContextManifest.model_validate(manifest)


def test_manifest_tampering_with_counts_ratio_or_digest_is_rejected(tmp_path: Path) -> None:
    result = _selection(tmp_path)
    assert result.package is not None
    original = result.package.manifest.model_dump(mode="python")
    for field, value in (
        ("retained_bytes", 999),
        ("retained_to_baseline_ratio", 0.99),
        ("included_count", 0),
    ):
        changed = dict(original)
        changed[field] = value
        with pytest.raises(ValidationError):
            ContextManifest.model_validate(changed)


def test_manifest_is_metadata_only_and_agent_projection_preserves_provenance(
    tmp_path: Path,
) -> None:
    result = _selection(tmp_path)
    assert result.package is not None
    manifest_json = canonical_context_bytes(result.package.manifest).decode("utf-8")
    references = result.package.agent_references()

    assert "VALUE = 1" not in manifest_json
    assert len(references) == 2
    assert references[0].trust is not None
    assert references[1].content == "VALUE = 1\n"
    assert references[1].content_sha256 is not None


def test_canonical_manifest_has_no_absolute_path_secret_or_usage_claim(tmp_path: Path) -> None:
    result = _selection(tmp_path)
    encoded = canonical_context_bytes(result).decode("utf-8")
    parsed = json.loads(encoded)

    assert str(tmp_path) not in encoded
    assert "token" not in encoded.lower()
    assert "cost" not in encoded.lower()
    assert parsed["package"]["manifest"]["repository_reference"] == "repo.contract"
