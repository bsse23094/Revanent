from __future__ import annotations

import ctypes
import json
import os
import shutil
import struct
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

import pytest

from revanent.commands import (
    CommandPolicy,
    EnvironmentPolicy,
    ExecutablePolicy,
    ExecutableRule,
    LocalCommandRunner,
    PathPolicy,
)
from revanent.git import LocalGitRepository, WorktreeOwnershipStore
from revanent.ports import (
    BranchCollisionError,
    CommandRequest,
    CommandResult,
    CommandRunner,
    CommandStatus,
    DirtyRepositoryError,
    EnvironmentOverrides,
    GitPathPolicyError,
    InvalidGitReferenceError,
    MalformedOwnershipRecordError,
    NotGitRepositoryError,
    OutputLimits,
    OwnershipMismatchError,
    OwnershipRecordConflictError,
    PartialWorktreeCreationError,
    ProtectedBranchError,
    StaleOwnershipRecordError,
    UnownedWorktreeError,
    UnsafeRepositoryStateError,
    UnsupportedGitRepositoryError,
    WorktreeCleanupRefusedError,
    WorktreeCollisionError,
    WorktreeCreationRequest,
    WorktreeId,
    WorktreeLifecycleStatus,
)

_GIT_KEYS = frozenset(
    {
        "GIT_CONFIG_NOSYSTEM",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_KEY_0",
        "GIT_CONFIG_VALUE_0",
        "GIT_CONFIG_KEY_1",
        "GIT_CONFIG_VALUE_1",
        "GIT_ATTR_NOSYSTEM",
        "GIT_TERMINAL_PROMPT",
        "GCM_INTERACTIVE",
        "GIT_PAGER",
        "PAGER",
        "GIT_EDITOR",
        "GIT_SEQUENCE_EDITOR",
        "LC_ALL",
        "LANG",
    }
)


@dataclass(frozen=True, slots=True)
class GitFixture:
    root: Path
    repository: Path
    worktree_root: Path
    state_root: Path
    runner: LocalCommandRunner
    adapter: LocalGitRepository
    store: WorktreeOwnershipStore

    def git(
        self,
        arguments: tuple[str, ...],
        *,
        cwd: Path | None = None,
        expected_exit_codes: tuple[int, ...] = (0,),
    ) -> CommandResult:
        result = self.runner.run(
            CommandRequest(
                executable="git",
                arguments=arguments,
                working_directory=cwd or self.repository,
                correlation_id=f"fixture-{os.urandom(8).hex()}",
                environment=EnvironmentOverrides(),
                timeout_seconds=30,
                output_limits=OutputLimits(
                    stdout_bytes=2 * 1_024 * 1_024,
                    stderr_bytes=2 * 1_024 * 1_024,
                    artifact_bytes_per_stream=1,
                ),
                expected_exit_codes=expected_exit_codes,
            )
        )
        assert result.status is CommandStatus.SUCCESS, result.stderr.text
        return result

    def request(
        self,
        *,
        worktree_id: WorktreeId | None = None,
        target: Path | None = None,
        branch: str | None = None,
        revision: str = "HEAD",
    ) -> WorktreeCreationRequest:
        identifier = worktree_id or WorktreeId.new()
        return WorktreeCreationRequest(
            source_path=self.repository,
            target_path=target or self.worktree_root / str(identifier),
            worktree_id=identifier,
            run_id="run_" + "a" * 32,
            branch_name=branch or f"revanent/{identifier}",
            base_revision=revision,
        )


def _selected_baseline() -> dict[str, str]:
    keys = ("SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR")
    return {key: os.environ[key] for key in keys if key in os.environ}


def _runner(tmp_path: Path) -> LocalCommandRunner:
    raw_git = shutil.which("git")
    if raw_git is None:
        pytest.fail("the Git integration suite requires Git")
    git = Path(raw_git).resolve(strict=True)
    extensions = (git.suffix,) if os.name == "nt" else ()
    return LocalCommandRunner(
        executable_policy=ExecutablePolicy(
            (ExecutableRule("git", (git,), allowed_extensions=extensions),)
        ),
        path_policy=PathPolicy((tmp_path.resolve(strict=True),)),
        environment_policy=EnvironmentPolicy(
            baseline=_selected_baseline(),
            allowed_override_keys=_GIT_KEYS,
        ),
        command_policy=CommandPolicy(
            max_timeout_seconds=60,
            max_stdout_bytes=8 * 1_024 * 1_024,
            max_stderr_bytes=8 * 1_024 * 1_024,
            max_artifact_bytes_per_stream=1,
            allow_artifacts=False,
        ),
    )


def _make_fixture(tmp_path: Path) -> GitFixture:
    repository = tmp_path / "repository with spaces"
    worktree_root = tmp_path / "owned worktrees"
    state_root = tmp_path / "ownership records"
    repository.mkdir()
    worktree_root.mkdir()
    state_root.mkdir()
    runner = _runner(tmp_path)
    policy = PathPolicy((tmp_path.resolve(strict=True),))
    store = WorktreeOwnershipStore(state_root.resolve(strict=True))
    adapter = LocalGitRepository(
        runner=runner,
        path_policy=policy,
        worktree_root=worktree_root.resolve(strict=True),
        ownership_store=store,
    )
    fixture = GitFixture(
        root=tmp_path.resolve(strict=True),
        repository=repository.resolve(strict=True),
        worktree_root=worktree_root.resolve(strict=True),
        state_root=state_root.resolve(strict=True),
        runner=runner,
        adapter=adapter,
        store=store,
    )
    fixture.git(("init", "--initial-branch=main"))
    fixture.git(("config", "--local", "user.name", "Revanent Tests"))
    fixture.git(("config", "--local", "user.email", "revanent@example.invalid"))
    fixture.git(("config", "--local", "core.autocrlf", "false"))
    (repository / "tracked.txt").write_text("initial\n", encoding="utf-8")
    fixture.git(("add", "--", "tracked.txt"))
    fixture.git(("commit", "-m", "initial"))
    return fixture


@pytest.fixture
def git_fixture(tmp_path: Path) -> GitFixture:
    return _make_fixture(tmp_path)


def test_discovers_repository_identity_and_rejects_non_repository_and_bare(
    git_fixture: GitFixture,
) -> None:
    nested = git_fixture.repository / "nested"
    nested.mkdir()
    identity = git_fixture.adapter.discover(nested)
    assert identity.worktree_root == git_fixture.repository
    assert identity.common_git_directory == (git_fixture.repository / ".git").resolve(strict=True)
    assert identity.object_format == "sha1"
    assert identity.repository_id.startswith("repo_")

    outside = git_fixture.root / "not a repository"
    outside.mkdir()
    with pytest.raises(NotGitRepositoryError):
        git_fixture.adapter.discover(outside)

    bare = git_fixture.root / "bare.git"
    bare.mkdir()
    git_fixture.git(("init", "--bare"), cwd=bare)
    with pytest.raises(UnsupportedGitRepositoryError):
        git_fixture.adapter.discover(bare)


def test_inspects_attached_and_detached_head(git_fixture: GitFixture) -> None:
    attached = git_fixture.adapter.inspect(git_fixture.repository)

    assert attached.branch == "main"
    assert not attached.detached_head
    assert attached.status.is_clean
    assert attached.head_commit == attached.identity.root_commits[0]

    git_fixture.git(("checkout", "--detach", "HEAD"))
    detached = git_fixture.adapter.inspect(git_fixture.repository)

    assert detached.branch is None
    assert detached.detached_head


def test_inspects_local_upstream_and_default_branch_without_network(
    git_fixture: GitFixture,
) -> None:
    git_fixture.git(("update-ref", "refs/remotes/origin/main", "HEAD"))
    git_fixture.git(("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main"))
    git_fixture.git(("config", "--local", "remote.origin.url", "../local-only"))
    git_fixture.git(
        (
            "config",
            "--local",
            "remote.origin.fetch",
            "+refs/heads/*:refs/remotes/origin/*",
        )
    )
    git_fixture.git(("config", "--local", "branch.main.remote", "origin"))
    git_fixture.git(("config", "--local", "branch.main.merge", "refs/heads/main"))

    snapshot = git_fixture.adapter.inspect(git_fixture.repository)

    assert snapshot.upstream == "origin/main"
    assert snapshot.default_branch == "main"


def test_status_detects_staged_unstaged_untracked_ignored_and_special_names(
    git_fixture: GitFixture,
) -> None:
    (git_fixture.repository / "tracked.txt").write_text("staged\n", encoding="utf-8")
    git_fixture.git(("add", "--", "tracked.txt"))
    (git_fixture.repository / "tracked.txt").write_text("unstaged after stage\n", encoding="utf-8")
    special = "space quote' dollar$ amp& unicode-λ.txt"
    (git_fixture.repository / special).write_text("untracked\n", encoding="utf-8")
    (git_fixture.repository / ".gitignore").write_text("ignored file.txt\n", encoding="utf-8")
    git_fixture.git(("add", "--", ".gitignore"))
    (git_fixture.repository / "ignored file.txt").write_text("ignored\n", encoding="utf-8")

    status = git_fixture.adapter.inspect(git_fixture.repository).status

    assert "tracked.txt" in status.staged_paths
    assert "tracked.txt" in status.unstaged_paths
    assert status.untracked_paths == (special,)
    assert status.ignored_paths == ("ignored file.txt",)


@pytest.mark.skipif(os.name == "nt", reason="Win32 filenames cannot contain newlines or tabs")
def test_status_retains_newline_and_tab_filenames_on_posix(git_fixture: GitFixture) -> None:
    names = ("tab\tname.txt", "line\nname.txt")
    for name in names:
        (git_fixture.repository / name).write_text("special\n", encoding="utf-8")

    status = git_fixture.adapter.inspect(git_fixture.repository).status

    assert status.untracked_paths == tuple(sorted(names))


def test_inspection_detects_conflict_and_merge_state(git_fixture: GitFixture) -> None:
    git_fixture.git(("checkout", "-b", "other"))
    (git_fixture.repository / "tracked.txt").write_text("other\n", encoding="utf-8")
    git_fixture.git(("add", "--", "tracked.txt"))
    git_fixture.git(("commit", "-m", "other"))
    git_fixture.git(("checkout", "main"))
    (git_fixture.repository / "tracked.txt").write_text("main\n", encoding="utf-8")
    git_fixture.git(("add", "--", "tracked.txt"))
    git_fixture.git(("commit", "-m", "main"))
    git_fixture.git(("merge", "other"), expected_exit_codes=(0, 1))

    status = git_fixture.adapter.inspect(git_fixture.repository).status

    assert status.conflicted_paths == ("tracked.txt",)
    assert status.merge_in_progress
    assert not status.is_clean


@pytest.mark.parametrize(
    ("marker", "attribute", "content"),
    (
        ("CHERRY_PICK_HEAD", "cherry_pick_in_progress", "HEAD"),
        ("REVERT_HEAD", "revert_in_progress", "HEAD"),
        ("BISECT_START", "bisect_in_progress", "main"),
    ),
)
def test_inspection_detects_operation_markers(
    git_fixture: GitFixture,
    marker: str,
    attribute: str,
    content: str,
) -> None:
    value = (
        git_fixture.adapter.inspect(git_fixture.repository).head_commit
        if content == "HEAD"
        else content
    )
    (git_fixture.repository / ".git" / marker).write_text(value + "\n", encoding="ascii")

    status = git_fixture.adapter.inspect(git_fixture.repository).status

    assert getattr(status, attribute) is True


def test_inspection_detects_rebase_and_sequencer_directories(git_fixture: GitFixture) -> None:
    (git_fixture.repository / ".git" / "rebase-merge").mkdir()
    (git_fixture.repository / ".git" / "sequencer").mkdir()

    status = git_fixture.adapter.inspect(git_fixture.repository).status

    assert status.rebase_in_progress
    assert status.sequencer_in_progress


def test_create_verify_commit_and_normal_cleanup_preserve_original_and_branch(
    git_fixture: GitFixture,
) -> None:
    request = git_fixture.request()
    before = git_fixture.adapter.inspect(git_fixture.repository)

    created = git_fixture.adapter.create_worktree(request)

    assert created.record.lifecycle_status is WorktreeLifecycleStatus.ACTIVE
    assert created.worktree.path == request.target_path
    assert created.worktree.branch == request.branch_name
    assert created.worktree.head_commit == before.head_commit
    after_create = git_fixture.adapter.inspect(git_fixture.repository)
    assert after_create.head_commit == before.head_commit
    assert after_create.branch == "main"
    assert after_create.status.is_clean
    assert git_fixture.store.record_path(request.worktree_id).is_file()

    (request.target_path / "owned.txt").write_text("owned commit\n", encoding="utf-8")
    git_fixture.git(("add", "--", "owned.txt"), cwd=request.target_path)
    git_fixture.git(("commit", "-m", "owned"), cwd=request.target_path)
    verified = git_fixture.adapter.verify_owned_worktree(request.worktree_id)
    assert verified.worktree.head_commit != before.head_commit
    assert verified.repository.status.is_clean

    cleanup = git_fixture.adapter.cleanup_worktree(request.worktree_id)

    assert cleanup.status.value == "REMOVED"
    assert cleanup.record.lifecycle_status is WorktreeLifecycleStatus.REMOVED
    assert not request.target_path.exists()
    branch = git_fixture.git(
        ("show-ref", "--verify", "--quiet", f"refs/heads/{request.branch_name}"),
        expected_exit_codes=(0, 1),
    )
    assert branch.exit_code == 0
    assert git_fixture.store.load(request.worktree_id) == cleanup.record
    repeated = git_fixture.adapter.cleanup_worktree(request.worktree_id)
    assert repeated.status.value == "ALREADY_REMOVED"


def test_linked_worktree_uses_same_common_repository_identity(git_fixture: GitFixture) -> None:
    request = git_fixture.request()
    created = git_fixture.adapter.create_worktree(request)

    source = git_fixture.adapter.discover(git_fixture.repository)
    linked = git_fixture.adapter.discover(created.worktree.path)

    assert linked.repository_id == source.repository_id
    assert linked.common_git_directory == source.common_git_directory
    assert linked.worktree_root != source.worktree_root


@pytest.mark.parametrize("kind", ("staged", "unstaged", "untracked"))
def test_dirty_source_creation_is_refused_without_an_ownership_record(
    git_fixture: GitFixture,
    kind: str,
) -> None:
    request = git_fixture.request()
    if kind == "untracked":
        (git_fixture.repository / "untracked.txt").write_text("user\n", encoding="utf-8")
    else:
        (git_fixture.repository / "tracked.txt").write_text("user\n", encoding="utf-8")
        if kind == "staged":
            git_fixture.git(("add", "--", "tracked.txt"))

    with pytest.raises(DirtyRepositoryError):
        git_fixture.adapter.create_worktree(request)

    assert not git_fixture.store.record_path(request.worktree_id).exists()
    assert not request.target_path.exists()


def test_protected_branch_is_rejected_but_protected_base_is_allowed(
    git_fixture: GitFixture,
) -> None:
    protected = git_fixture.request(branch="main")
    with pytest.raises(ProtectedBranchError):
        git_fixture.adapter.create_worktree(protected)

    allowed = git_fixture.request(revision="refs/heads/main")
    result = git_fixture.adapter.create_worktree(allowed)

    assert (
        result.record.base_commit == git_fixture.adapter.inspect(git_fixture.repository).head_commit
    )
    assert result.record.branch_name.startswith("revanent/")


@pytest.mark.parametrize(
    ("branch", "revision"),
    (
        ("-option", "HEAD"),
        ("revanent/../escape", "HEAD"),
        ("revanent/task", "-HEAD"),
        ("revanent/task", "main"),
    ),
)
def test_invalid_branch_or_revision_is_rejected_before_ownership(
    git_fixture: GitFixture,
    branch: str,
    revision: str,
) -> None:
    request = git_fixture.request(branch=branch, revision=revision)

    with pytest.raises((InvalidGitReferenceError, ProtectedBranchError)):
        git_fixture.adapter.create_worktree(request)

    assert not git_fixture.store.record_path(request.worktree_id).exists()


def test_branch_target_registry_and_record_collisions_are_refused(git_fixture: GitFixture) -> None:
    first = git_fixture.request()
    git_fixture.adapter.create_worktree(first)

    with pytest.raises(OwnershipRecordConflictError):
        git_fixture.adapter.create_worktree(first)

    same_branch = git_fixture.request(branch=first.branch_name)
    with pytest.raises(BranchCollisionError):
        git_fixture.adapter.create_worktree(same_branch)

    same_target = git_fixture.request(target=first.target_path)
    with pytest.raises(WorktreeCollisionError):
        git_fixture.adapter.create_worktree(same_target)


def test_existing_target_directory_is_refused(git_fixture: GitFixture) -> None:
    request = git_fixture.request()
    request.target_path.mkdir()

    with pytest.raises(WorktreeCollisionError):
        git_fixture.adapter.create_worktree(request)


def test_target_traversal_and_string_prefix_sibling_are_rejected(git_fixture: GitFixture) -> None:
    traversal = git_fixture.request(target=git_fixture.worktree_root / ".." / "escape")
    sibling = git_fixture.root / "owned worktrees-sibling"
    sibling.mkdir()
    prefix_attack = git_fixture.request(target=sibling / "escape")

    for request in (traversal, prefix_attack):
        with pytest.raises(GitPathPolicyError):
            git_fixture.adapter.create_worktree(request)
        assert not git_fixture.store.record_path(request.worktree_id).exists()


def test_link_escape_from_worktree_root_is_rejected(git_fixture: GitFixture) -> None:
    outside = git_fixture.root / "outside"
    outside.mkdir()
    link = git_fixture.worktree_root / "escape-link"
    if os.name == "nt":
        _create_windows_junction(link, outside)
    else:
        link.symlink_to(outside, target_is_directory=True)
    request = git_fixture.request(target=link / "target")

    with pytest.raises(GitPathPolicyError):
        git_fixture.adapter.create_worktree(request)


def test_unc_repository_is_rejected_by_default_without_access(git_fixture: GitFixture) -> None:
    with pytest.raises(GitPathPolicyError):
        git_fixture.adapter.discover(Path(r"\\server\share\repository"))


def test_dirty_and_untracked_owned_worktree_cleanup_is_refused_and_record_remains(
    git_fixture: GitFixture,
) -> None:
    request = git_fixture.request()
    git_fixture.adapter.create_worktree(request)
    untracked = request.target_path / "user work.txt"
    untracked.write_text("preserve\n", encoding="utf-8")

    with pytest.raises(WorktreeCleanupRefusedError):
        git_fixture.adapter.cleanup_worktree(request.worktree_id)

    assert untracked.is_file()
    assert git_fixture.store.load(request.worktree_id).lifecycle_status is (
        WorktreeLifecycleStatus.ACTIVE
    )


def test_ignored_file_cleanup_is_refused(git_fixture: GitFixture) -> None:
    (git_fixture.repository / ".gitignore").write_text("*.ignored\n", encoding="utf-8")
    git_fixture.git(("add", "--", ".gitignore"))
    git_fixture.git(("commit", "-m", "ignore fixture"))
    request = git_fixture.request()
    git_fixture.adapter.create_worktree(request)
    ignored = request.target_path / "user.ignored"
    ignored.write_text("preserve\n", encoding="utf-8")

    with pytest.raises(WorktreeCleanupRefusedError):
        git_fixture.adapter.cleanup_worktree(request.worktree_id)

    assert ignored.is_file()


def test_in_progress_operation_cleanup_is_refused(git_fixture: GitFixture) -> None:
    request = git_fixture.request()
    git_fixture.adapter.create_worktree(request)
    identity = git_fixture.adapter.discover(request.target_path)
    (identity.git_directory / "BISECT_START").write_text("fixture\n", encoding="ascii")

    with pytest.raises(WorktreeCleanupRefusedError):
        git_fixture.adapter.cleanup_worktree(request.worktree_id)


def test_in_progress_source_operation_blocks_creation(git_fixture: GitFixture) -> None:
    head = git_fixture.adapter.inspect(git_fixture.repository).head_commit
    (git_fixture.repository / ".git" / "CHERRY_PICK_HEAD").write_text(head + "\n", encoding="ascii")
    request = git_fixture.request()

    with pytest.raises(UnsafeRepositoryStateError):
        git_fixture.adapter.create_worktree(request)

    assert not git_fixture.store.record_path(request.worktree_id).exists()


def test_locked_owned_worktree_cleanup_is_refused(git_fixture: GitFixture) -> None:
    request = git_fixture.request()
    git_fixture.adapter.create_worktree(request)
    git_fixture.git(("worktree", "lock", "--reason", "fixture lock", str(request.target_path)))

    with pytest.raises(WorktreeCleanupRefusedError):
        git_fixture.adapter.cleanup_worktree(request.worktree_id)


def test_cleanup_racing_with_new_user_file_fails_without_force(git_fixture: GitFixture) -> None:
    request = git_fixture.request()
    git_fixture.adapter.create_worktree(request)
    raced_file = request.target_path / "raced user file.txt"
    racing = LocalGitRepository(
        runner=_DirtyBeforeWorktreeRemove(git_fixture.runner, raced_file),
        path_policy=PathPolicy((git_fixture.root,)),
        worktree_root=git_fixture.worktree_root,
        ownership_store=git_fixture.store,
    )

    with pytest.raises(WorktreeCleanupRefusedError):
        racing.cleanup_worktree(request.worktree_id)

    assert raced_file.read_text(encoding="utf-8") == "preserve\n"
    assert git_fixture.store.load(request.worktree_id).lifecycle_status is (
        WorktreeLifecycleStatus.ACTIVE
    )


def test_stale_record_after_external_normal_removal_is_refused(git_fixture: GitFixture) -> None:
    request = git_fixture.request()
    git_fixture.adapter.create_worktree(request)
    git_fixture.git(("worktree", "remove", "--", str(request.target_path)))

    with pytest.raises(StaleOwnershipRecordError):
        git_fixture.adapter.cleanup_worktree(request.worktree_id)

    assert git_fixture.store.load(request.worktree_id).lifecycle_status is (
        WorktreeLifecycleStatus.ACTIVE
    )


def test_unowned_and_partial_records_cannot_be_cleaned(git_fixture: GitFixture) -> None:
    unknown = WorktreeId.new()
    with pytest.raises(UnownedWorktreeError):
        git_fixture.adapter.cleanup_worktree(unknown)

    request = git_fixture.request()
    failing = LocalGitRepository(
        runner=_FailAfterWorktreeAdd(git_fixture.runner),
        path_policy=PathPolicy((git_fixture.root,)),
        worktree_root=git_fixture.worktree_root,
        ownership_store=git_fixture.store,
    )
    with pytest.raises(PartialWorktreeCreationError):
        failing.create_worktree(request)
    assert git_fixture.store.load(request.worktree_id).lifecycle_status is (
        WorktreeLifecycleStatus.PARTIAL
    )
    with pytest.raises(StaleOwnershipRecordError):
        git_fixture.adapter.cleanup_worktree(request.worktree_id)


def test_tampered_path_repository_and_branch_records_are_rejected(git_fixture: GitFixture) -> None:
    request = git_fixture.request()
    git_fixture.adapter.create_worktree(request)
    path = git_fixture.store.record_path(request.worktree_id)
    original = json.loads(path.read_text(encoding="utf-8"))

    mutations = (
        {"worktree_path": str(git_fixture.root / "outside")},
        {"repository": {**original["repository"], "repository_id": "repo_" + "f" * 64}},
        {"branch_name": "revanent/tampered"},
    )
    for mutation in mutations:
        payload = {**original, **mutation}
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises((GitPathPolicyError, OwnershipMismatchError, StaleOwnershipRecordError)):
            git_fixture.adapter.verify_owned_worktree(request.worktree_id)
    path.write_text(json.dumps(original), encoding="utf-8")


def test_unknown_schema_and_malformed_ownership_records_are_rejected(
    git_fixture: GitFixture,
) -> None:
    request = git_fixture.request()
    git_fixture.adapter.create_worktree(request)
    path = git_fixture.store.record_path(request.worktree_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema_version"] = 2
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(MalformedOwnershipRecordError):
        git_fixture.adapter.verify_owned_worktree(request.worktree_id)


def test_replaced_worktree_repository_is_rejected_without_deletion(git_fixture: GitFixture) -> None:
    request = git_fixture.request()
    git_fixture.adapter.create_worktree(request)
    preserved = git_fixture.worktree_root / "preserved-original"
    request.target_path.rename(preserved)
    request.target_path.mkdir()
    git_fixture.git(("init", "--initial-branch=main"), cwd=request.target_path)

    with pytest.raises(OwnershipMismatchError):
        git_fixture.adapter.verify_owned_worktree(request.worktree_id)

    assert request.target_path.is_dir()
    assert preserved.is_dir()


def test_same_worktree_id_concurrent_creation_has_one_owner(git_fixture: GitFixture) -> None:
    request = git_fixture.request()

    def create() -> object:
        try:
            return git_fixture.adapter.create_worktree(request)
        except Exception as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(lambda _: create(), range(2)))

    assert sum(not isinstance(result, Exception) for result in results) == 1, results
    assert sum(isinstance(result, OwnershipRecordConflictError) for result in results) == 1


def test_same_branch_concurrent_creation_has_at_most_one_worktree(git_fixture: GitFixture) -> None:
    branch = "revanent/concurrent-branch"
    requests = (git_fixture.request(branch=branch), git_fixture.request(branch=branch))

    def create(request: WorktreeCreationRequest) -> object:
        try:
            return git_fixture.adapter.create_worktree(request)
        except Exception as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(create, requests))

    assert sum(not isinstance(result, Exception) for result in results) <= 1
    assert all(
        not isinstance(result, Exception)
        or isinstance(result, BranchCollisionError | PartialWorktreeCreationError)
        for result in results
    )
    live = git_fixture.adapter.inspect(git_fixture.repository)
    assert sum(item.branch == branch for item in live.worktrees) <= 1


def test_same_target_concurrent_creation_has_at_most_one_worktree(git_fixture: GitFixture) -> None:
    target = git_fixture.worktree_root / "shared-target"
    requests = (git_fixture.request(target=target), git_fixture.request(target=target))

    def create(request: WorktreeCreationRequest) -> object:
        try:
            return git_fixture.adapter.create_worktree(request)
        except Exception as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(create, requests))

    assert sum(not isinstance(result, Exception) for result in results) <= 1
    assert all(
        not isinstance(result, Exception)
        or isinstance(result, WorktreeCollisionError | PartialWorktreeCreationError)
        for result in results
    )
    live = git_fixture.adapter.inspect(git_fixture.repository)
    assert sum(item.path == target for item in live.worktrees) <= 1


def test_concurrent_cleanup_removes_once_and_never_forces(git_fixture: GitFixture) -> None:
    request = git_fixture.request()
    git_fixture.adapter.create_worktree(request)

    def cleanup() -> object:
        try:
            return git_fixture.adapter.cleanup_worktree(request.worktree_id)
        except Exception as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(lambda _: cleanup(), range(2)))

    assert sum(not isinstance(result, Exception) for result in results) >= 1
    assert all(
        not isinstance(result, Exception)
        or isinstance(result, OwnershipRecordConflictError | StaleOwnershipRecordError)
        for result in results
    )
    assert not request.target_path.exists()


def test_source_change_between_inspection_and_creation_is_preserved_as_partial(
    git_fixture: GitFixture,
) -> None:
    request = git_fixture.request()
    user_file = git_fixture.repository / "raced source file.txt"
    racing = LocalGitRepository(
        runner=_DirtyAfterFilterCheck(git_fixture.runner, user_file),
        path_policy=PathPolicy((git_fixture.root,)),
        worktree_root=git_fixture.worktree_root,
        ownership_store=git_fixture.store,
    )

    with pytest.raises(PartialWorktreeCreationError):
        racing.create_worktree(request)

    assert user_file.read_text(encoding="utf-8") == "preserve\n"
    assert not request.target_path.exists()
    assert git_fixture.store.load(request.worktree_id).lifecycle_status is (
        WorktreeLifecycleStatus.PARTIAL
    )


def test_checkout_filters_are_rejected_before_process_execution(git_fixture: GitFixture) -> None:
    marker = git_fixture.root / "filter-ran"
    git_fixture.git(("config", "--local", "filter.evil.smudge", f"touch {marker}"))
    request = git_fixture.request()

    with pytest.raises(UnsafeRepositoryStateError):
        git_fixture.adapter.create_worktree(request)

    assert not marker.exists()
    assert not git_fixture.store.record_path(request.worktree_id).exists()


def test_repository_post_checkout_hook_is_neutralized(git_fixture: GitFixture) -> None:
    marker = git_fixture.root / "hook-ran"
    hook = git_fixture.repository / ".git" / "hooks" / "post-checkout"
    hook.write_text(
        f"#!/bin/sh\nprintf hit > '{marker.as_posix()}'\n",
        encoding="utf-8",
        newline="\n",
    )
    hook.chmod(0o755)

    git_fixture.adapter.create_worktree(git_fixture.request())

    assert not marker.exists()


def test_in_repository_internal_roots_must_already_be_ignored(git_fixture: GitFixture) -> None:
    internal = git_fixture.repository / ".revanent"
    worktrees = internal / "worktrees"
    state = internal / "ownership"
    worktrees.mkdir(parents=True)
    state.mkdir()
    adapter = LocalGitRepository(
        runner=git_fixture.runner,
        path_policy=PathPolicy((git_fixture.root,)),
        worktree_root=worktrees.resolve(strict=True),
        ownership_store=WorktreeOwnershipStore(state.resolve(strict=True)),
    )
    request = WorktreeCreationRequest(
        source_path=git_fixture.repository,
        target_path=worktrees / str(WorktreeId.new()),
        worktree_id=WorktreeId.new(),
        branch_name="revanent/internal-root",
    )

    with pytest.raises(UnsafeRepositoryStateError):
        adapter.create_worktree(request)


def test_git_unavailable_is_reported_as_typed_error(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    worktrees = tmp_path / "worktrees"
    state = tmp_path / "state"
    repository.mkdir()
    worktrees.mkdir()
    state.mkdir()
    missing = tmp_path / ("missing.exe" if os.name == "nt" else "missing")
    runner = LocalCommandRunner(
        executable_policy=ExecutablePolicy(
            (
                ExecutableRule(
                    "git",
                    (missing.resolve(strict=False),),
                    allowed_extensions=(".exe",) if os.name == "nt" else (),
                ),
            )
        ),
        path_policy=PathPolicy((tmp_path.resolve(strict=True),)),
        environment_policy=EnvironmentPolicy(
            baseline=_selected_baseline(),
            allowed_override_keys=_GIT_KEYS,
        ),
    )
    adapter = LocalGitRepository(
        runner=runner,
        path_policy=PathPolicy((tmp_path.resolve(strict=True),)),
        worktree_root=worktrees.resolve(strict=True),
        ownership_store=WorktreeOwnershipStore(state.resolve(strict=True)),
    )

    from revanent.ports import GitUnavailableError

    with pytest.raises(GitUnavailableError):
        adapter.discover(repository.resolve(strict=True))


class _FailAfterWorktreeAdd(CommandRunner):
    def __init__(self, delegate: CommandRunner) -> None:
        self._delegate = delegate
        self._fail_next = False

    def run(self, request: CommandRequest) -> CommandResult:
        if self._fail_next:
            self._fail_next = False
            raise RuntimeError("simulated verification interruption")
        result = self._delegate.run(request)
        if request.arguments[:2] == ("worktree", "add") and result.succeeded:
            self._fail_next = True
        return result


class _DirtyBeforeWorktreeRemove(CommandRunner):
    def __init__(self, delegate: CommandRunner, target: Path) -> None:
        self._delegate = delegate
        self._target = target

    def run(self, request: CommandRequest) -> CommandResult:
        if request.arguments[:2] == ("worktree", "remove"):
            self._target.write_text("preserve\n", encoding="utf-8")
        return self._delegate.run(request)


class _DirtyAfterFilterCheck(CommandRunner):
    def __init__(self, delegate: CommandRunner, target: Path) -> None:
        self._delegate = delegate
        self._target = target
        self._dirtied = False

    def run(self, request: CommandRequest) -> CommandResult:
        result = self._delegate.run(request)
        if request.arguments[:2] == ("config", "--local") and not self._dirtied:
            self._target.write_text("preserve\n", encoding="utf-8")
            self._dirtied = True
        return result


def _create_windows_junction(link: Path, target: Path) -> None:
    link.mkdir()
    substitute = f"\\??\\{target.resolve(strict=True)}".encode("utf-16-le")
    print_name = str(target.resolve(strict=True)).encode("utf-16-le")
    path_buffer = substitute + b"\x00\x00" + print_name + b"\x00\x00"
    data = (
        struct.pack(
            "<LHHHHHH",
            0xA0000003,
            8 + len(path_buffer),
            0,
            0,
            len(substitute),
            len(substitute) + 2,
            len(print_name),
        )
        + path_buffer
    )
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.DeviceIoControl.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    )
    kernel32.DeviceIoControl.restype = wintypes.BOOL
    handle = kernel32.CreateFileW(
        str(link),
        0x40000000,
        0,
        None,
        3,
        0x00200000 | 0x02000000,
        None,
    )
    if handle == wintypes.HANDLE(-1).value:
        raise OSError(ctypes.get_last_error(), "cannot open junction directory")
    try:
        returned = wintypes.DWORD()
        buffer = ctypes.create_string_buffer(data)
        if not kernel32.DeviceIoControl(
            handle,
            0x000900A4,
            buffer,
            len(data),
            None,
            0,
            ctypes.byref(returned),
            None,
        ):
            raise OSError(ctypes.get_last_error(), "cannot create directory junction")
    finally:
        kernel32.CloseHandle(handle)
