from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from revanent.git import ProtectedBranchPolicy, WorktreeOwnershipStore
from revanent.git.parsing import parse_status_porcelain_v2, parse_worktree_porcelain
from revanent.git.policy import validate_branch_name, validate_revision
from revanent.ports import (
    GitErrorCategory,
    InvalidGitReferenceError,
    MalformedGitOutputError,
    MalformedOwnershipRecordError,
    OwnershipRecordConflictError,
    ProtectedBranchError,
    RepositoryIdentity,
    RepositoryStatus,
    WorktreeId,
    WorktreeLifecycleStatus,
    WorktreeOwnershipRecord,
)

COMMIT = "1" * 40


def _identity(tmp_path: Path) -> RepositoryIdentity:
    root = tmp_path.resolve(strict=True)
    git_directory = root / ".git"
    git_directory.mkdir(exist_ok=True)
    return RepositoryIdentity(
        repository_id="repo_" + "a" * 64,
        worktree_root=root,
        git_directory=git_directory,
        common_git_directory=git_directory,
        object_format="sha1",
        root_commits=(COMMIT,),
    )


def _record(tmp_path: Path, worktree_id: WorktreeId | None = None) -> WorktreeOwnershipRecord:
    return WorktreeOwnershipRecord(
        worktree_id=worktree_id or WorktreeId("wt_" + "1" * 32),
        run_id="run_" + "2" * 32,
        repository=_identity(tmp_path),
        worktree_path=tmp_path.resolve(strict=True) / "worktree",
        branch_name="revanent/task",
        base_commit=COMMIT,
        created_head=COMMIT,
        created_at=datetime(2026, 7, 30, tzinfo=UTC),
        revanent_version="0.1.0.dev0",
        lifecycle_status=WorktreeLifecycleStatus.ACTIVE,
    )


def test_worktree_id_is_stable_and_validated() -> None:
    generated = WorktreeId.new()

    assert str(generated).startswith("wt_")
    assert len(str(generated)) == 35
    with pytest.raises(ValidationError):
        WorktreeId("../escape")


@pytest.mark.parametrize(
    "branch",
    (
        "-option",
        "revanent/../escape",
        "revanent//double",
        "revanent/name.lock",
        "revanent/@{one}",
        "revanent/back\\slash",
    ),
)
def test_invalid_or_unowned_branch_names_are_rejected(branch: str) -> None:
    with pytest.raises(InvalidGitReferenceError):
        validate_branch_name(branch)


@pytest.mark.parametrize(
    "revision",
    ("-HEAD", "main", "HEAD~1", "HEAD^{commit}", "refs/remotes/origin/main", "../main"),
)
def test_ambiguous_or_option_like_revisions_are_rejected(revision: str) -> None:
    with pytest.raises(InvalidGitReferenceError):
        validate_revision(revision)


def test_safe_branches_and_revisions_are_accepted() -> None:
    assert validate_branch_name("revanent/task-1") == "revanent/task-1"
    assert validate_revision("HEAD") == "HEAD"
    assert validate_revision(COMMIT) == COMMIT
    assert validate_revision("refs/heads/main") == "refs/heads/main"


def test_protected_branch_policy_is_configurable_and_owned_namespace_is_mutable() -> None:
    policy = ProtectedBranchPolicy(
        exact_names=frozenset({"trunk"}),
        namespace_patterns=("release/*",),
    )

    assert policy.is_protected("trunk")
    assert policy.is_protected("release/1.0")
    assert policy.is_protected("default", default_branch="default")
    assert (
        policy.require_mutable_owned_branch("revanent/task", default_branch="trunk")
        == "revanent/task"
    )
    with pytest.raises(ProtectedBranchError):
        policy.require_mutable_owned_branch("revanent/task", default_branch="revanent/task")


def test_status_parser_retains_special_paths_without_shell_or_quote_parsing() -> None:
    special = "space tab\tquote' dollar$ unicode-λ.txt"
    text = (
        f"# branch.oid {COMMIT}\x00"
        "# branch.head main\x00"
        "# branch.upstream origin/main\x00"
        f"1 M. N... 100644 100644 100644 {COMMIT} {COMMIT} staged.txt\x00"
        f"1 .M N... 100644 100644 100644 {COMMIT} {COMMIT} unstaged.txt\x00"
        f"? {special}\x00"
        "! ignored file.txt\x00"
    )

    parsed = parse_status_porcelain_v2(text)

    assert parsed.branch == "main"
    assert parsed.upstream == "origin/main"
    assert parsed.status.staged_paths == ("staged.txt",)
    assert parsed.status.unstaged_paths == ("unstaged.txt",)
    assert parsed.status.untracked_paths == (special,)
    assert parsed.status.ignored_paths == ("ignored file.txt",)


def test_status_parser_handles_rename_source_and_detached_head() -> None:
    text = (
        f"# branch.oid {COMMIT}\x00"
        "# branch.head (detached)\x00"
        f"2 R. N... 100644 100644 100644 {COMMIT} {COMMIT} R100 new name\x00"
        "old name\x00"
    )

    parsed = parse_status_porcelain_v2(text)

    assert parsed.detached
    assert parsed.branch is None
    assert parsed.status.staged_paths == ("new name",)


@pytest.mark.parametrize(
    "text",
    (
        "",
        f"# branch.oid {COMMIT}\x00# branch.head main",
        f"# branch.oid {COMMIT}\x00# branch.head main\x00unknown\x00",
        f"# branch.oid {COMMIT}\x00# branch.head main\x00? bad\ufffdname\x00",
    ),
)
def test_status_parser_fails_explicitly_on_malformed_or_incomplete_output(text: str) -> None:
    with pytest.raises(MalformedGitOutputError):
        parse_status_porcelain_v2(text)


def test_worktree_parser_handles_spaces_and_registry_flags(tmp_path: Path) -> None:
    first = (tmp_path / "main worktree").resolve(strict=False)
    second = (tmp_path / "linked worktree").resolve(strict=False)
    text = (
        f"worktree {first}\x00HEAD {COMMIT}\x00branch refs/heads/main\x00\x00"
        f"worktree {second}\x00HEAD {COMMIT}\x00detached\x00locked reason here\x00\x00"
    )

    parsed = parse_worktree_porcelain(text)

    by_path = {item.path: item for item in parsed}
    assert by_path[first].branch == "main"
    assert by_path[second].detached
    assert by_path[second].locked_reason == "reason here"


def test_repository_status_cleanliness_counts_changes_and_operations() -> None:
    assert RepositoryStatus().is_clean
    assert not RepositoryStatus(untracked_paths=("new",)).is_clean
    assert not RepositoryStatus(rebase_in_progress=True).is_clean


def test_ownership_store_atomically_round_trips_and_rejects_conflicts(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    store = WorktreeOwnershipStore(state.resolve(strict=True))
    record = _record(tmp_path)

    with store.acquire(record.worktree_id) as lease:
        lease.write(record, replace=False)
        assert lease.load() == record
        with pytest.raises(OwnershipRecordConflictError):
            lease.write(record, replace=False)

    assert store.load(record.worktree_id) == record
    assert not tuple(state.glob("*.tmp"))
    assert not tuple(state.glob("*.lock"))


def test_ownership_store_rejects_concurrent_or_stale_lock(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    store = WorktreeOwnershipStore(state.resolve(strict=True))
    worktree_id = WorktreeId("wt_" + "3" * 32)

    with (
        store.acquire(worktree_id),
        pytest.raises(OwnershipRecordConflictError),
        store.acquire(worktree_id),
    ):
        pytest.fail("a second lease must not be acquired")


@pytest.mark.parametrize(
    "mutation",
    (
        {"schema_version": 2},
        {"unexpected": True},
        {"worktree_id": "wt_" + "9" * 32},
    ),
)
def test_ownership_store_rejects_unsupported_malformed_or_mismatched_records(
    tmp_path: Path,
    mutation: dict[str, object],
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    store = WorktreeOwnershipStore(state.resolve(strict=True))
    record = _record(tmp_path)
    payload = record.model_dump(mode="json")
    payload.update(mutation)
    store.record_path(record.worktree_id).write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(MalformedOwnershipRecordError):
        store.load(record.worktree_id)


def test_ownership_lifecycle_fields_are_strict(tmp_path: Path) -> None:
    values = _record(tmp_path).model_dump()
    values["lifecycle_status"] = WorktreeLifecycleStatus.PARTIAL

    with pytest.raises(ValidationError):
        WorktreeOwnershipRecord.model_validate(values)

    values["last_error_category"] = GitErrorCategory.GIT_COMMAND
    assert WorktreeOwnershipRecord.model_validate(values).lifecycle_status is (
        WorktreeLifecycleStatus.PARTIAL
    )
