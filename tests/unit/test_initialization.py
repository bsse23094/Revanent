from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Barrier

import pytest

from revanent.application import initialization
from revanent.application.initialization import InitializationAction, InitializationService


@dataclass(frozen=True, slots=True)
class _Identity:
    worktree_root: Path


@dataclass(frozen=True, slots=True)
class _Status:
    has_changes: bool = False
    operation_in_progress: bool = False


@dataclass(frozen=True, slots=True)
class _Snapshot:
    identity: _Identity
    status: _Status = _Status()


@pytest.fixture
def initialized_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[InitializationService, Path]:
    root = tmp_path / "repository with spaces"
    root.mkdir()
    snapshot = _Snapshot(_Identity(root))
    monkeypatch.setattr(initialization, "inspect_repository", lambda _: snapshot)
    monkeypatch.setattr(initialization, "repository_path_is_ignored", lambda _root, _path: True)
    return InitializationService(), root


def test_plan_is_side_effect_free_and_describes_only_owned_paths(
    initialized_service: tuple[InitializationService, Path],
) -> None:
    service, root = initialized_service

    result = service.plan(root)

    assert result.succeeded is True
    assert result.plan is not None
    assert not (root / "revanent.yaml").exists()
    assert [resource.relative_path for resource in result.plan.resources] == [
        "revanent.yaml",
        ".revanent",
        ".revanent/worktrees",
        ".revanent/runs",
        ".revanent/state",
    ]
    assert all(resource.action is InitializationAction.CREATE for resource in result.plan.resources)


def test_initialization_is_idempotent_and_never_rewrites_identical_config(
    initialized_service: tuple[InitializationService, Path],
) -> None:
    service, root = initialized_service

    first = service.initialize(root)
    config = root / "revanent.yaml"
    first_bytes = config.read_bytes()
    second = service.initialize(root)

    assert first.succeeded is True and first.changed is True
    assert second.succeeded is True and second.changed is False
    assert config.read_bytes() == first_bytes
    assert (root / ".revanent/worktrees").is_dir()
    assert (root / ".revanent/runs").is_dir()
    assert (root / ".revanent/state").is_dir()


def test_differing_existing_configuration_refuses_without_overwrite(
    initialized_service: tuple[InitializationService, Path],
) -> None:
    service, root = initialized_service
    config = root / "revanent.yaml"
    config.write_text("user-owned: true\n", encoding="utf-8")

    result = service.initialize(root)

    assert result.succeeded is False
    assert result.code == "INITIALIZATION_CONFLICT"
    assert config.read_text(encoding="utf-8") == "user-owned: true\n"


def test_unignored_owned_root_refuses_without_creating_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    snapshot = _Snapshot(_Identity(root))
    monkeypatch.setattr(initialization, "inspect_repository", lambda _: snapshot)
    monkeypatch.setattr(initialization, "repository_path_is_ignored", lambda _root, _path: False)

    result = InitializationService().initialize(root)

    assert result.succeeded is False
    assert result.code == "OWNED_ROOT_NOT_IGNORED"
    assert not (root / ".revanent").exists()


def test_dirty_repository_refuses_before_initialization_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    snapshot = _Snapshot(_Identity(root), _Status(has_changes=True))
    monkeypatch.setattr(initialization, "inspect_repository", lambda _: snapshot)
    monkeypatch.setattr(initialization, "repository_path_is_ignored", lambda _root, _path: True)

    result = InitializationService().initialize(root)

    assert result.succeeded is False
    assert result.code == "REPOSITORY_UNSAFE"
    assert not (root / ".revanent").exists()


def test_concurrent_initialization_never_observes_partial_yaml(
    initialized_service: tuple[InitializationService, Path],
) -> None:
    service, root = initialized_service
    barrier = Barrier(2)

    def invoke() -> tuple[bool, str]:
        barrier.wait()
        result = service.initialize(root)
        return result.succeeded, result.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(lambda _: invoke(), range(2)))

    config = root / "revanent.yaml"
    assert config.exists()
    assert "schema_version: 1" in config.read_text(encoding="utf-8")
    assert any(succeeded for succeeded, _ in outcomes)
    assert all(code in {"initialized", "INITIALIZATION_CONFLICT"} for _, code in outcomes)
