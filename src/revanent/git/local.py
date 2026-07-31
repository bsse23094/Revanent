"""Concrete non-destructive Git repository and owned-worktree adapter."""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from revanent import __version__
from revanent.commands.policy import PathPolicy
from revanent.git.ownership import OwnershipLease, WorktreeOwnershipStore
from revanent.git.parsing import parse_status_porcelain_v2, parse_worktree_porcelain
from revanent.git.policy import ProtectedBranchPolicy, validate_revision
from revanent.ports.commands import (
    CommandFailureCategory,
    CommandRequest,
    CommandResult,
    CommandRunner,
    CommandStatus,
    EnvironmentOverrides,
    EnvironmentVariable,
    OutputLimits,
    WorkingDirectoryPolicyError,
)
from revanent.ports.git import (
    BranchCollisionError,
    DirtyRepositoryError,
    GitCommandError,
    GitError,
    GitErrorCategory,
    GitOperationStatus,
    GitPathPolicyError,
    GitUnavailableError,
    InvalidGitReferenceError,
    MalformedGitOutputError,
    NotGitRepositoryError,
    OwnershipMismatchError,
    OwnershipRecordConflictError,
    PartialWorktreeCreationError,
    RepositoryIdentity,
    RepositorySnapshot,
    RepositoryStatus,
    StaleOwnershipRecordError,
    UnsafeRepositoryStateError,
    UnsupportedGitRepositoryError,
    WorktreeCleanupRefusedError,
    WorktreeCleanupResult,
    WorktreeCollisionError,
    WorktreeCreationRequest,
    WorktreeCreationResult,
    WorktreeId,
    WorktreeLifecycleStatus,
    WorktreeOwnershipRecord,
    WorktreeVerificationResult,
)

_GIT_OUTPUT_LIMIT = 8 * 1_024 * 1_024
_GIT_TIMEOUT_SECONDS = 60.0
_UNSAFE_FILTER_PATTERN = r"^filter\..*\.(clean|smudge|process)$"
_ALLOWED_SUBCOMMANDS = frozenset(
    {
        "check-ignore",
        "check-ref-format",
        "config",
        "merge-base",
        "rev-list",
        "rev-parse",
        "show-ref",
        "status",
        "symbolic-ref",
        "worktree",
    }
)
_FORBIDDEN_ARGUMENTS = frozenset(
    {
        "--delete",
        "--force",
        "--hard",
        "--prune",
        "-D",
        "-f",
    }
)


class LocalGitRepository:
    """Inspect Git and manage only live-verified Revanent-owned worktrees."""

    def __init__(
        self,
        *,
        runner: CommandRunner,
        path_policy: PathPolicy,
        worktree_root: Path,
        ownership_store: WorktreeOwnershipStore,
        protected_branches: ProtectedBranchPolicy | None = None,
        git_executable: str = "git",
        timeout_seconds: float = _GIT_TIMEOUT_SECONDS,
        allow_unc: bool = False,
    ) -> None:
        if not git_executable or any(separator in git_executable for separator in "/\\"):
            raise ValueError("Git executable must be a configured simple capability name")
        if not 0 < timeout_seconds <= _GIT_TIMEOUT_SECONDS:
            raise ValueError("Git timeout must be positive and at most 60 seconds")
        try:
            resolved_worktree_root = path_policy.resolve_working_directory(worktree_root)
        except WorkingDirectoryPolicyError as error:
            raise ValueError("worktree root is not approved by the command path policy") from error
        if _is_unc(resolved_worktree_root) and not allow_unc:
            raise ValueError("UNC worktree roots require explicit authorization")
        self._runner = runner
        self._paths = path_policy
        self._worktree_root = resolved_worktree_root
        self._ownership = ownership_store
        self._protected = protected_branches or ProtectedBranchPolicy()
        self._git_executable = git_executable
        self._timeout = timeout_seconds
        self._allow_unc = allow_unc

    def discover(self, path: Path) -> RepositoryIdentity:
        """Discover canonical worktree/common-Git identity without mutation."""
        working_directory = self._approved_directory(path)
        bare_result = self._run(
            working_directory,
            ("rev-parse", "--is-bare-repository"),
            expected_exit_codes=(0, 128),
        )
        if bare_result.exit_code == 128:
            raise NotGitRepositoryError("path is not inside a Git repository")
        if self._single_value(bare_result, "bare-repository probe") == "true":
            raise UnsupportedGitRepositoryError("bare Git repositories are not supported")
        inside_result = self._run(
            working_directory,
            ("rev-parse", "--is-inside-work-tree"),
            expected_exit_codes=(0, 128),
        )
        if (
            inside_result.exit_code == 128
            or self._single_value(inside_result, "working-tree probe") != "true"
        ):
            raise NotGitRepositoryError("path is not inside a Git working tree")
        worktree_root = self._git_path(
            working_directory,
            ("rev-parse", "--path-format=absolute", "--show-toplevel"),
            "working-tree root",
        )
        git_directory = self._git_path(
            worktree_root,
            ("rev-parse", "--path-format=absolute", "--git-dir"),
            "Git directory",
        )
        common_git_directory = self._git_path(
            worktree_root,
            ("rev-parse", "--path-format=absolute", "--git-common-dir"),
            "common Git directory",
        )
        if not _contains(common_git_directory, git_directory):
            raise UnsupportedGitRepositoryError("Git directory is outside its common repository")
        object_format = self._single_value(
            self._run(worktree_root, ("rev-parse", "--show-object-format")),
            "object format",
        )
        if object_format not in {"sha1", "sha256"}:
            raise UnsupportedGitRepositoryError("Git object format is unsupported")
        typed_object_format = cast(Literal["sha1", "sha256"], object_format)
        head = self._resolve_commit(worktree_root, "HEAD")
        roots_result = self._run(worktree_root, ("rev-list", "--max-parents=0", head))
        root_commits = self._line_values(roots_result, "root commits")
        if not root_commits or any(not _is_commit(value, object_format) for value in root_commits):
            raise MalformedGitOutputError("Git root-commit output is malformed")
        normalized_common = os.path.normcase(str(common_git_directory)).replace("\\", "/")
        identity_material = json.dumps(
            {
                "common_git_directory": normalized_common,
                "object_format": object_format,
                "root_commits": sorted(root_commits),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        repository_id = "repo_" + hashlib.sha256(identity_material).hexdigest()
        return RepositoryIdentity(
            repository_id=repository_id,
            worktree_root=worktree_root,
            git_directory=git_directory,
            common_git_directory=common_git_directory,
            object_format=typed_object_format,
            root_commits=tuple(sorted(root_commits)),
        )

    def inspect(self, path: Path) -> RepositorySnapshot:
        """Return one deterministic repository/worktree snapshot."""
        identity = self.discover(path)
        status_result = self._run(
            identity.worktree_root,
            (
                "status",
                "--porcelain=v2",
                "-z",
                "--branch",
                "--untracked-files=all",
                "--ignored=matching",
            ),
        )
        parsed = parse_status_porcelain_v2(status_result.stdout.text)
        if not _is_commit(parsed.head_commit, identity.object_format):
            raise MalformedGitOutputError("Git status HEAD is malformed")
        head_commit = self._resolve_commit(identity.worktree_root, "HEAD")
        if parsed.head_commit != head_commit:
            raise UnsafeRepositoryStateError("repository HEAD changed during inspection")
        worktrees = parse_worktree_porcelain(
            self._run(
                identity.worktree_root,
                ("worktree", "list", "--porcelain", "-z"),
            ).stdout.text
        )
        default_branch = self._default_branch(identity.worktree_root)
        operation_status = self._operation_status(identity)
        status = RepositoryStatus(
            staged_paths=parsed.status.staged_paths,
            unstaged_paths=parsed.status.unstaged_paths,
            untracked_paths=parsed.status.untracked_paths,
            conflicted_paths=parsed.status.conflicted_paths,
            ignored_paths=parsed.status.ignored_paths,
            merge_in_progress=operation_status["merge_in_progress"],
            rebase_in_progress=operation_status["rebase_in_progress"],
            cherry_pick_in_progress=operation_status["cherry_pick_in_progress"],
            revert_in_progress=operation_status["revert_in_progress"],
            bisect_in_progress=operation_status["bisect_in_progress"],
            sequencer_in_progress=operation_status["sequencer_in_progress"],
        )
        return RepositorySnapshot(
            identity=identity,
            branch=parsed.branch,
            detached_head=parsed.detached,
            head_commit=head_commit,
            upstream=parsed.upstream,
            default_branch=default_branch,
            status=status,
            worktrees=worktrees,
        )

    def create_worktree(self, request: WorktreeCreationRequest) -> WorktreeCreationResult:
        """Create and verify one branch/worktree while preserving partial evidence."""
        source = self.inspect(request.source_path)
        self._require_safe_source(source)
        branch = self._protected.require_mutable_owned_branch(
            request.branch_name,
            default_branch=source.default_branch,
        )
        self._check_branch_with_git(source.identity.worktree_root, branch)
        revision = validate_revision(request.base_revision)
        base_commit = self._resolve_commit(source.identity.worktree_root, revision)
        target = self._validate_target(request.target_path, must_exist=False)
        self._ensure_internal_roots_do_not_dirty_source(source)
        self._ensure_checkout_configuration_safe(source.identity.worktree_root)
        if os.path.lexists(self._ownership.record_path(request.worktree_id)):
            raise OwnershipRecordConflictError(
                "an ownership record already exists for the requested worktree",
                worktree_id=request.worktree_id,
            )
        self._require_no_collisions(source, target=target, branch=branch)
        creating_record = WorktreeOwnershipRecord(
            worktree_id=request.worktree_id,
            run_id=request.run_id,
            repository=source.identity,
            worktree_path=target,
            branch_name=branch,
            base_commit=base_commit,
            created_head=base_commit,
            created_at=datetime.now(UTC),
            revanent_version=__version__,
            lifecycle_status=WorktreeLifecycleStatus.CREATING,
        )
        with self._ownership.acquire(request.worktree_id) as lease:
            if lease.exists():
                raise OwnershipRecordConflictError(
                    "an ownership record already exists for the requested worktree",
                    worktree_id=request.worktree_id,
                )
            lease.write(creating_record, replace=False)
            try:
                current = self.inspect(source.identity.worktree_root)
                self._require_safe_source(current)
                if not _same_repository(source.identity, current.identity):
                    raise UnsafeRepositoryStateError("repository identity changed before creation")
                if self._resolve_commit(current.identity.worktree_root, revision) != base_commit:
                    raise UnsafeRepositoryStateError("base revision changed before creation")
                self._require_no_collisions(current, target=target, branch=branch)
                self._run(
                    current.identity.worktree_root,
                    ("worktree", "add", "-b", branch, "--", str(target), base_commit),
                )
                verified = self._verify_record(creating_record, require_active=False)
                if verified.worktree.head_commit != base_commit:
                    raise OwnershipMismatchError(
                        "created worktree HEAD does not match its immutable base",
                        worktree_id=request.worktree_id,
                    )
                active_record = _replace_record(
                    creating_record,
                    lifecycle_status=WorktreeLifecycleStatus.ACTIVE,
                )
                lease.write(active_record, replace=True)
                return WorktreeCreationResult(
                    status=GitOperationStatus.CREATED,
                    record=active_record,
                    worktree=verified.worktree,
                )
            except GitError as error:
                self._preserve_partial(lease, creating_record, error.category)
                raise PartialWorktreeCreationError(
                    "worktree creation was incomplete; ownership evidence was preserved",
                    worktree_id=request.worktree_id,
                ) from error
            except Exception as error:
                self._preserve_partial(lease, creating_record, GitErrorCategory.GIT_COMMAND)
                raise PartialWorktreeCreationError(
                    "worktree creation was incomplete; ownership evidence was preserved",
                    worktree_id=request.worktree_id,
                ) from error

    def verify_owned_worktree(self, worktree_id: WorktreeId) -> WorktreeVerificationResult:
        record = self._ownership.load(worktree_id)
        return self._verify_record(record, require_active=True)

    def cleanup_worktree(self, worktree_id: WorktreeId) -> WorktreeCleanupResult:
        """Normally remove only a clean, verified, owned worktree; retain its branch."""
        with self._ownership.acquire(worktree_id) as lease:
            record = lease.load()
            if record.lifecycle_status is WorktreeLifecycleStatus.REMOVED:
                self._verify_removed_record(record)
                return WorktreeCleanupResult(
                    status=GitOperationStatus.ALREADY_REMOVED,
                    record=record,
                )
            if record.lifecycle_status is not WorktreeLifecycleStatus.ACTIVE:
                raise StaleOwnershipRecordError(
                    "partial or creating ownership records require manual recovery",
                    worktree_id=worktree_id,
                )
            verified = self._verify_record(record, require_active=True)
            if not verified.repository.status.is_clean:
                raise WorktreeCleanupRefusedError(
                    "owned worktree has changes or an in-progress Git operation",
                    worktree_id=worktree_id,
                )
            if verified.repository.status.ignored_paths:
                raise WorktreeCleanupRefusedError(
                    "owned worktree contains ignored files that normal cleanup could destroy",
                    worktree_id=worktree_id,
                )
            if verified.worktree.locked_reason is not None:
                raise WorktreeCleanupRefusedError(
                    "owned worktree is locked in the Git registry",
                    worktree_id=worktree_id,
                )
            try:
                self._run(
                    record.repository.worktree_root,
                    ("worktree", "remove", "--", str(record.worktree_path)),
                )
            except GitCommandError as error:
                raise WorktreeCleanupRefusedError(
                    "normal Git worktree removal was refused; no force retry was attempted",
                    worktree_id=worktree_id,
                ) from error
            source_after = self.inspect(record.repository.worktree_root)
            if any(_same_path(item.path, record.worktree_path) for item in source_after.worktrees):
                raise WorktreeCleanupRefusedError(
                    "Git still registers the worktree after normal removal",
                    worktree_id=worktree_id,
                )
            removed_record = _replace_record(
                record,
                lifecycle_status=WorktreeLifecycleStatus.REMOVED,
                cleanup_head=verified.worktree.head_commit,
                cleaned_at=datetime.now(UTC),
            )
            lease.write(removed_record, replace=True)
            return WorktreeCleanupResult(
                status=GitOperationStatus.REMOVED,
                record=removed_record,
            )

    def _verify_record(
        self,
        record: WorktreeOwnershipRecord,
        *,
        require_active: bool,
    ) -> WorktreeVerificationResult:
        if require_active and record.lifecycle_status is not WorktreeLifecycleStatus.ACTIVE:
            raise StaleOwnershipRecordError(
                "ownership record is not active",
                worktree_id=record.worktree_id,
            )
        target = self._validate_target(record.worktree_path, must_exist=False)
        if not target.exists() and not target.is_symlink():
            raise StaleOwnershipRecordError(
                "owned worktree path no longer exists",
                worktree_id=record.worktree_id,
            )
        target = self._validate_target(record.worktree_path, must_exist=True)
        try:
            source = self.inspect(record.repository.worktree_root)
            target_repository = self.inspect(target)
        except GitError as error:
            raise OwnershipMismatchError(
                "recorded repository or worktree cannot be verified",
                worktree_id=record.worktree_id,
            ) from error
        if not _same_repository(record.repository, source.identity) or not _same_repository(
            record.repository, target_repository.identity
        ):
            raise OwnershipMismatchError(
                "live repository identity does not match ownership evidence",
                worktree_id=record.worktree_id,
            )
        if not _same_path(target_repository.identity.worktree_root, target):
            raise OwnershipMismatchError(
                "live worktree root does not match ownership evidence",
                worktree_id=record.worktree_id,
            )
        matches = [item for item in source.worktrees if _same_path(item.path, target)]
        if len(matches) != 1:
            raise StaleOwnershipRecordError(
                "owned worktree is not uniquely registered by Git",
                worktree_id=record.worktree_id,
            )
        worktree = matches[0]
        if (
            worktree.branch != record.branch_name
            or target_repository.branch != record.branch_name
            or worktree.head_commit != target_repository.head_commit
        ):
            raise OwnershipMismatchError(
                "live branch or HEAD does not match ownership evidence",
                worktree_id=record.worktree_id,
            )
        self._protected.require_mutable_owned_branch(
            record.branch_name,
            default_branch=source.default_branch,
        )
        ancestry = self._run(
            target,
            ("merge-base", "--is-ancestor", record.base_commit, target_repository.head_commit),
            expected_exit_codes=(0, 1),
        )
        if ancestry.exit_code != 0:
            raise OwnershipMismatchError(
                "owned worktree HEAD is not descended from its recorded base",
                worktree_id=record.worktree_id,
            )
        return WorktreeVerificationResult(
            status=GitOperationStatus.VERIFIED,
            record=record,
            worktree=worktree,
            repository=target_repository,
        )

    def _verify_removed_record(self, record: WorktreeOwnershipRecord) -> None:
        try:
            source = self.inspect(record.repository.worktree_root)
        except GitError as error:
            raise OwnershipMismatchError(
                "cleaned ownership record no longer matches a live repository",
                worktree_id=record.worktree_id,
            ) from error
        if not _same_repository(record.repository, source.identity):
            raise OwnershipMismatchError(
                "cleaned ownership record repository identity changed",
                worktree_id=record.worktree_id,
            )
        if any(_same_path(item.path, record.worktree_path) for item in source.worktrees):
            raise StaleOwnershipRecordError(
                "cleaned ownership record path is registered again",
                worktree_id=record.worktree_id,
            )

    def _preserve_partial(
        self,
        lease: OwnershipLease,
        record: WorktreeOwnershipRecord,
        category: GitErrorCategory,
    ) -> None:
        partial = _replace_record(
            record,
            lifecycle_status=WorktreeLifecycleStatus.PARTIAL,
            last_error_category=category,
        )
        with suppress(Exception):
            lease.write(partial, replace=True)

    def _require_safe_source(self, snapshot: RepositorySnapshot) -> None:
        if snapshot.status.conflicted_paths or snapshot.status.operation_in_progress:
            raise UnsafeRepositoryStateError(
                "repository has conflicts or an in-progress Git operation"
            )
        if snapshot.status.has_changes:
            raise DirtyRepositoryError(
                "repository contains unexplained staged, unstaged, or untracked work"
            )

    def _require_no_collisions(
        self,
        snapshot: RepositorySnapshot,
        *,
        target: Path,
        branch: str,
    ) -> None:
        if os.path.lexists(target):
            raise WorktreeCollisionError("worktree target already exists")
        if any(_same_path(item.path, target) for item in snapshot.worktrees):
            raise WorktreeCollisionError("worktree target is already registered")
        if any(item.branch == branch for item in snapshot.worktrees):
            raise BranchCollisionError("worktree branch is checked out elsewhere")
        result = self._run(
            snapshot.identity.worktree_root,
            ("show-ref", "--verify", "--quiet", f"refs/heads/{branch}"),
            expected_exit_codes=(0, 1),
        )
        if result.exit_code == 0:
            raise BranchCollisionError("worktree branch already exists")

    def _check_branch_with_git(self, repository_root: Path, branch: str) -> None:
        result = self._run(
            repository_root,
            ("check-ref-format", "--branch", branch),
            expected_exit_codes=(0, 1, 128),
        )
        if result.exit_code != 0:
            raise InvalidGitReferenceError("Git rejected the dedicated branch name")

    def _ensure_checkout_configuration_safe(self, repository_root: Path) -> None:
        result = self._run(
            repository_root,
            (
                "config",
                "--local",
                "--includes",
                "--name-only",
                "--null",
                "--get-regexp",
                _UNSAFE_FILTER_PATTERN,
            ),
            expected_exit_codes=(0, 1),
        )
        if result.exit_code == 0 and result.stdout.text:
            raise UnsafeRepositoryStateError(
                "repository-configured checkout filters are not allowed for worktree creation"
            )

    def _ensure_internal_roots_do_not_dirty_source(self, source: RepositorySnapshot) -> None:
        for root in (self._worktree_root, self._ownership.root):
            if not _contains(source.identity.worktree_root, root):
                continue
            result = self._run(
                source.identity.worktree_root,
                ("check-ignore", "--quiet", "--no-index", "--", str(root)),
                expected_exit_codes=(0, 1),
            )
            if result.exit_code != 0:
                raise UnsafeRepositoryStateError(
                    "Revanent state inside the source worktree must already be ignored"
                )

    def _resolve_commit(self, repository_root: Path, revision: str) -> str:
        validate_revision(revision)
        result = self._run(
            repository_root,
            ("rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"),
            expected_exit_codes=(0, 128),
        )
        if result.exit_code != 0:
            raise InvalidGitReferenceError("base revision does not resolve to a local commit")
        commit = self._single_value(result, "resolved commit").lower()
        if not _is_commit(commit):
            raise MalformedGitOutputError("Git returned a malformed commit identifier")
        return commit

    def _default_branch(self, repository_root: Path) -> str | None:
        result = self._run(
            repository_root,
            ("symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"),
            expected_exit_codes=(0, 1, 128),
        )
        if result.exit_code != 0:
            return None
        value = self._single_value(result, "default branch")
        prefix = "origin/"
        return value.removeprefix(prefix) if value.startswith(prefix) else value

    @staticmethod
    def _operation_status(identity: RepositoryIdentity) -> dict[str, bool]:
        git_directory = identity.git_directory
        return {
            "merge_in_progress": (git_directory / "MERGE_HEAD").exists(),
            "rebase_in_progress": (git_directory / "rebase-apply").exists()
            or (git_directory / "rebase-merge").exists(),
            "cherry_pick_in_progress": (git_directory / "CHERRY_PICK_HEAD").exists(),
            "revert_in_progress": (git_directory / "REVERT_HEAD").exists(),
            "bisect_in_progress": (git_directory / "BISECT_START").exists(),
            "sequencer_in_progress": (git_directory / "sequencer").exists(),
        }

    def _validate_target(self, path: Path, *, must_exist: bool) -> Path:
        if not isinstance(path, Path) or not path.is_absolute():
            raise GitPathPolicyError("worktree target must be an absolute pathlib path")
        if _is_unc(path) and not self._allow_unc:
            raise GitPathPolicyError("UNC worktree targets are not authorized")
        try:
            relative = path.relative_to(self._worktree_root)
            if relative == Path("."):
                raise GitPathPolicyError("worktree target cannot replace the worktree root")
            resolved = self._paths.resolve_relative(
                self._worktree_root,
                relative,
                must_exist=must_exist,
            )
        except GitPathPolicyError:
            raise
        except (ValueError, WorkingDirectoryPolicyError) as error:
            raise GitPathPolicyError("worktree target is outside the approved root") from error
        if not must_exist:
            try:
                parent = resolved.parent.resolve(strict=True)
            except (OSError, RuntimeError) as error:
                raise GitPathPolicyError("worktree target parent must already exist") from error
            if not _contains(self._worktree_root, parent):
                raise GitPathPolicyError("worktree target parent escapes the approved root")
        return resolved

    def _approved_directory(self, path: Path) -> Path:
        if _is_unc(path) and not self._allow_unc:
            raise GitPathPolicyError("UNC repositories are not authorized")
        try:
            return self._paths.resolve_working_directory(path)
        except WorkingDirectoryPolicyError as error:
            raise GitPathPolicyError("repository path is outside approved roots") from error

    def _git_path(
        self,
        repository_root: Path,
        arguments: tuple[str, ...],
        label: str,
    ) -> Path:
        raw_path = self._single_value(self._run(repository_root, arguments), label)
        try:
            path = Path(raw_path).resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise MalformedGitOutputError(f"Git {label} is unavailable") from error
        if not path.is_absolute() or (_is_unc(path) and not self._allow_unc):
            raise GitPathPolicyError(f"Git {label} is outside the authorized filesystem form")
        return path

    def _run(
        self,
        working_directory: Path,
        arguments: tuple[str, ...],
        *,
        expected_exit_codes: tuple[int, ...] = (0,),
    ) -> CommandResult:
        self._validate_command_surface(arguments)
        request = CommandRequest(
            executable=self._git_executable,
            arguments=arguments,
            working_directory=working_directory,
            correlation_id=f"git-{arguments[0]}-{os.urandom(8).hex()}",
            environment=self._git_environment(),
            timeout_seconds=self._timeout,
            output_limits=OutputLimits(
                stdout_bytes=_GIT_OUTPUT_LIMIT,
                stderr_bytes=_GIT_OUTPUT_LIMIT,
                artifact_bytes_per_stream=1,
            ),
            expected_exit_codes=expected_exit_codes,
        )
        result = self._runner.run(request)
        if result.status is not CommandStatus.SUCCESS:
            category = result.failure.category if result.failure is not None else None
            if category is CommandFailureCategory.EXECUTABLE_UNAVAILABLE:
                raise GitUnavailableError("configured Git executable is unavailable")
            if category is CommandFailureCategory.WORKING_DIRECTORY:
                raise GitPathPolicyError("Git working directory was rejected by command policy")
            raise GitCommandError("controlled Git command did not complete successfully")
        for stream in (result.stdout, result.stderr):
            if stream.truncated or stream.redaction_truncated:
                raise MalformedGitOutputError("controlled Git output exceeded its bounded capture")
        return result

    def _git_environment(self) -> EnvironmentOverrides:
        values = (
            ("GIT_CONFIG_NOSYSTEM", "1"),
            ("GIT_CONFIG_GLOBAL", os.devnull),
            ("GIT_CONFIG_COUNT", "2"),
            ("GIT_CONFIG_KEY_0", "core.hooksPath"),
            ("GIT_CONFIG_VALUE_0", str(self._ownership.disabled_hooks_path)),
            ("GIT_CONFIG_KEY_1", "core.fsmonitor"),
            ("GIT_CONFIG_VALUE_1", "false"),
            ("GIT_ATTR_NOSYSTEM", "1"),
            ("GIT_TERMINAL_PROMPT", "0"),
            ("GCM_INTERACTIVE", "Never"),
            ("GIT_PAGER", "cat"),
            ("PAGER", "cat"),
            ("GIT_EDITOR", "true"),
            ("GIT_SEQUENCE_EDITOR", "true"),
            ("LC_ALL", "C"),
            ("LANG", "C"),
        )
        return EnvironmentOverrides(tuple(EnvironmentVariable(key, value) for key, value in values))

    @staticmethod
    def _validate_command_surface(arguments: tuple[str, ...]) -> None:
        if not arguments or arguments[0] not in _ALLOWED_SUBCOMMANDS:
            raise GitCommandError("Git subcommand is outside the frozen P2-002 command surface")
        if any(argument in _FORBIDDEN_ARGUMENTS for argument in arguments):
            raise GitCommandError("destructive Git argument is forbidden")
        if arguments[0] == "worktree" and (
            len(arguments) < 2 or arguments[1] not in {"list", "add", "remove"}
        ):
            raise GitCommandError("Git worktree operation is not authorized")
        if arguments[0] == "config" and not {
            "--local",
            "--name-only",
            "--get-regexp",
        }.issubset(arguments):
            raise GitCommandError("only bounded local Git configuration inspection is authorized")

    @staticmethod
    def _single_value(result: CommandResult, label: str) -> str:
        value = result.stdout.text
        if value.endswith("\r\n"):
            value = value[:-2]
        elif value.endswith("\n"):
            value = value[:-1]
        if not value or "\r" in value or "\n" in value or "\x00" in value or "\ufffd" in value:
            raise MalformedGitOutputError(f"Git {label} output is malformed")
        return value

    @staticmethod
    def _line_values(result: CommandResult, label: str) -> tuple[str, ...]:
        text = result.stdout.text
        if not text.endswith("\n") or "\x00" in text or "\ufffd" in text:
            raise MalformedGitOutputError(f"Git {label} output is incomplete")
        values = tuple(line.removesuffix("\r") for line in text[:-1].split("\n"))
        if any(not value or "\r" in value for value in values):
            raise MalformedGitOutputError(f"Git {label} output is malformed")
        return values


def _replace_record(
    record: WorktreeOwnershipRecord,
    **updates: object,
) -> WorktreeOwnershipRecord:
    values = record.model_dump()
    values.update(updates)
    return WorktreeOwnershipRecord.model_validate(values)


def _is_commit(value: str, object_format: str | None = None) -> bool:
    expected_length = 64 if object_format == "sha256" else 40 if object_format == "sha1" else None
    return (
        value == value.lower()
        and all(character in "0123456789abcdef" for character in value)
        and len(value) in ({expected_length} if expected_length is not None else {40, 64})
    )


def _contains(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _same_path(left: Path, right: Path) -> bool:
    try:
        return os.path.normcase(str(left.resolve(strict=False))) == os.path.normcase(
            str(right.resolve(strict=False))
        )
    except (OSError, RuntimeError):
        return False


def _same_repository(left: RepositoryIdentity, right: RepositoryIdentity) -> bool:
    return (
        left.repository_id == right.repository_id
        and _same_path(left.common_git_directory, right.common_git_directory)
        and left.object_format == right.object_format
        and left.root_commits == right.root_commits
    )


def _is_unc(path: Path) -> bool:
    return str(path).replace("/", "\\").startswith("\\\\")
