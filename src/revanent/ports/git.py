"""Provider-independent contracts for safe local Git worktrees."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Protocol, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator

GIT_SCHEMA_VERSION = 1
OWNERSHIP_SCHEMA_VERSION = 1

_COMMIT_ID = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_REPOSITORY_ID = re.compile(r"^repo_[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^run_[0-9a-f]{32}$")
_WORKTREE_ID = re.compile(r"^wt_[0-9a-f]{32}$")


class WorktreeId(RootModel[str]):
    """Stable identifier used for records, locks, and recovery."""

    model_config = ConfigDict(frozen=True)

    @model_validator(mode="after")
    def _validate_root(self) -> Self:
        if _WORKTREE_ID.fullmatch(self.root) is None:
            raise ValueError("invalid worktree identifier")
        return self

    @classmethod
    def new(cls) -> WorktreeId:
        return cls(f"wt_{uuid4().hex}")

    def __str__(self) -> str:
        return self.root


class GitOperationStatus(StrEnum):
    """Successful or preserved terminal lifecycle outcomes."""

    CREATED = "CREATED"
    VERIFIED = "VERIFIED"
    REMOVED = "REMOVED"
    ALREADY_REMOVED = "ALREADY_REMOVED"


class WorktreeLifecycleStatus(StrEnum):
    """Durable ownership lifecycle states."""

    CREATING = "CREATING"
    ACTIVE = "ACTIVE"
    PARTIAL = "PARTIAL"
    REMOVED = "REMOVED"


class GitErrorCategory(StrEnum):
    """Sanitized failure categories suitable for recovery decisions."""

    GIT_UNAVAILABLE = "GIT_UNAVAILABLE"
    NOT_A_REPOSITORY = "NOT_A_REPOSITORY"
    UNSUPPORTED_REPOSITORY = "UNSUPPORTED_REPOSITORY"
    REPOSITORY_STATE_UNSAFE = "REPOSITORY_STATE_UNSAFE"
    DIRTY_STATE = "DIRTY_STATE"
    PROTECTED_BRANCH = "PROTECTED_BRANCH"
    INVALID_REFERENCE = "INVALID_REFERENCE"
    WORKTREE_COLLISION = "WORKTREE_COLLISION"
    BRANCH_COLLISION = "BRANCH_COLLISION"
    OWNERSHIP_RECORD_CONFLICT = "OWNERSHIP_RECORD_CONFLICT"
    OWNERSHIP_MISMATCH = "OWNERSHIP_MISMATCH"
    UNOWNED_WORKTREE = "UNOWNED_WORKTREE"
    STALE_OWNERSHIP_RECORD = "STALE_OWNERSHIP_RECORD"
    PATH_POLICY = "PATH_POLICY"
    GIT_COMMAND = "GIT_COMMAND"
    PARTIAL_CREATION = "PARTIAL_CREATION"
    CLEANUP_REFUSED = "CLEANUP_REFUSED"
    MALFORMED_OUTPUT = "MALFORMED_OUTPUT"
    MALFORMED_OWNERSHIP_RECORD = "MALFORMED_OWNERSHIP_RECORD"


class GitError(Exception):
    """Base sanitized Git boundary error."""

    category = GitErrorCategory.GIT_COMMAND

    def __init__(self, message: str, *, worktree_id: WorktreeId | None = None) -> None:
        if not message or "\x00" in message:
            raise ValueError("Git error messages must be non-empty and sanitized")
        super().__init__(message)
        self.worktree_id = worktree_id


class GitUnavailableError(GitError):
    category = GitErrorCategory.GIT_UNAVAILABLE


class NotGitRepositoryError(GitError):
    category = GitErrorCategory.NOT_A_REPOSITORY


class UnsupportedGitRepositoryError(GitError):
    category = GitErrorCategory.UNSUPPORTED_REPOSITORY


class UnsafeRepositoryStateError(GitError):
    category = GitErrorCategory.REPOSITORY_STATE_UNSAFE


class DirtyRepositoryError(GitError):
    category = GitErrorCategory.DIRTY_STATE


class ProtectedBranchError(GitError):
    category = GitErrorCategory.PROTECTED_BRANCH


class InvalidGitReferenceError(GitError):
    category = GitErrorCategory.INVALID_REFERENCE


class WorktreeCollisionError(GitError):
    category = GitErrorCategory.WORKTREE_COLLISION


class BranchCollisionError(GitError):
    category = GitErrorCategory.BRANCH_COLLISION


class OwnershipRecordConflictError(GitError):
    category = GitErrorCategory.OWNERSHIP_RECORD_CONFLICT


class OwnershipMismatchError(GitError):
    category = GitErrorCategory.OWNERSHIP_MISMATCH


class UnownedWorktreeError(GitError):
    category = GitErrorCategory.UNOWNED_WORKTREE


class StaleOwnershipRecordError(GitError):
    category = GitErrorCategory.STALE_OWNERSHIP_RECORD


class GitPathPolicyError(GitError):
    category = GitErrorCategory.PATH_POLICY


class GitCommandError(GitError):
    category = GitErrorCategory.GIT_COMMAND


class PartialWorktreeCreationError(GitError):
    category = GitErrorCategory.PARTIAL_CREATION


class WorktreeCleanupRefusedError(GitError):
    category = GitErrorCategory.CLEANUP_REFUSED


class MalformedGitOutputError(GitError):
    category = GitErrorCategory.MALFORMED_OUTPUT


class MalformedOwnershipRecordError(GitError):
    category = GitErrorCategory.MALFORMED_OWNERSHIP_RECORD


class _GitModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


AbsolutePath = Path
CommitId = Annotated[str, Field(pattern=_COMMIT_ID.pattern)]


class RepositoryIdentity(_GitModel):
    """Live identity evidence shared by a common Git object database."""

    repository_id: Annotated[str, Field(pattern=_REPOSITORY_ID.pattern)]
    worktree_root: AbsolutePath
    git_directory: AbsolutePath
    common_git_directory: AbsolutePath
    object_format: Literal["sha1", "sha256"]
    root_commits: tuple[CommitId, ...]
    schema_version: Literal[1] = 1

    @field_validator("worktree_root", "git_directory", "common_git_directory")
    @classmethod
    def _paths_are_absolute(cls, value: Path) -> Path:
        if not value.is_absolute() or len(str(value)) > 4_096:
            raise ValueError("repository identity paths must be absolute")
        return value

    @field_validator("root_commits")
    @classmethod
    def _roots_are_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or len(value) != len(set(value)):
            raise ValueError("repository root commits must be non-empty and unique")
        return tuple(sorted(value))


class RepositoryStatus(_GitModel):
    """Deterministic porcelain-v2 working-tree and operation state."""

    staged_paths: tuple[str, ...] = ()
    unstaged_paths: tuple[str, ...] = ()
    untracked_paths: tuple[str, ...] = ()
    conflicted_paths: tuple[str, ...] = ()
    ignored_paths: tuple[str, ...] = ()
    merge_in_progress: bool = False
    rebase_in_progress: bool = False
    cherry_pick_in_progress: bool = False
    revert_in_progress: bool = False
    bisect_in_progress: bool = False
    sequencer_in_progress: bool = False
    schema_version: Literal[1] = 1

    @field_validator(
        "staged_paths",
        "unstaged_paths",
        "untracked_paths",
        "conflicted_paths",
        "ignored_paths",
    )
    @classmethod
    def _paths_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item or "\x00" in item for item in value):
            raise ValueError("status paths must be non-empty and NUL-free")
        if len(value) != len(set(value)):
            raise ValueError("status paths must be unique")
        return tuple(sorted(value))

    @property
    def has_changes(self) -> bool:
        return bool(
            self.staged_paths
            or self.unstaged_paths
            or self.untracked_paths
            or self.conflicted_paths
        )

    @property
    def operation_in_progress(self) -> bool:
        return any(
            (
                self.merge_in_progress,
                self.rebase_in_progress,
                self.cherry_pick_in_progress,
                self.revert_in_progress,
                self.bisect_in_progress,
                self.sequencer_in_progress,
            )
        )

    @property
    def is_clean(self) -> bool:
        return not self.has_changes and not self.operation_in_progress


class WorktreeSnapshot(_GitModel):
    """One entry from Git's porcelain worktree registry."""

    path: AbsolutePath
    head_commit: CommitId
    branch: str | None = Field(default=None, min_length=1, max_length=1_024)
    detached: bool = False
    bare: bool = False
    locked_reason: str | None = Field(default=None, max_length=2_048)
    prunable_reason: str | None = Field(default=None, max_length=2_048)
    schema_version: Literal[1] = 1

    @field_validator("path")
    @classmethod
    def _path_is_absolute(cls, value: Path) -> Path:
        if not value.is_absolute() or len(str(value)) > 4_096:
            raise ValueError("worktree paths must be absolute")
        return value

    @model_validator(mode="after")
    def _branch_state_is_consistent(self) -> Self:
        if self.detached == (self.branch is not None):
            raise ValueError("worktree must be either detached or attached to one branch")
        return self


class RepositorySnapshot(_GitModel):
    """Complete bounded inspection result used before Git mutations."""

    identity: RepositoryIdentity
    branch: str | None = Field(default=None, min_length=1, max_length=1_024)
    detached_head: bool
    head_commit: CommitId
    upstream: str | None = Field(default=None, min_length=1, max_length=2_048)
    default_branch: str | None = Field(default=None, min_length=1, max_length=1_024)
    status: RepositoryStatus
    worktrees: tuple[WorktreeSnapshot, ...]
    schema_version: Literal[1] = 1

    @model_validator(mode="after")
    def _head_state_is_consistent(self) -> Self:
        if self.detached_head == (self.branch is not None):
            raise ValueError("repository HEAD must be either detached or attached")
        return self


class WorktreeCreationRequest(_GitModel):
    """Typed request for one dedicated branch and linked worktree."""

    source_path: AbsolutePath
    target_path: AbsolutePath
    worktree_id: WorktreeId
    branch_name: Annotated[str, Field(min_length=1, max_length=255)]
    base_revision: Annotated[str, Field(min_length=1, max_length=1_024)] = "HEAD"
    run_id: Annotated[str, Field(pattern=_RUN_ID.pattern)] | None = None
    schema_version: Literal[1] = 1

    @field_validator("source_path", "target_path")
    @classmethod
    def _request_paths_are_absolute(cls, value: Path) -> Path:
        if not value.is_absolute() or len(str(value)) > 4_096:
            raise ValueError("worktree request paths must be absolute")
        return value


class WorktreeOwnershipRecord(_GitModel):
    """Versioned durable evidence; never sufficient without live Git verification."""

    worktree_id: WorktreeId
    run_id: Annotated[str, Field(pattern=_RUN_ID.pattern)] | None = None
    repository: RepositoryIdentity
    worktree_path: AbsolutePath
    branch_name: Annotated[str, Field(min_length=1, max_length=255)]
    base_commit: CommitId
    created_head: CommitId
    created_at: datetime
    revanent_version: Annotated[str, Field(min_length=1, max_length=128)]
    lifecycle_status: WorktreeLifecycleStatus
    last_error_category: GitErrorCategory | None = None
    cleanup_head: CommitId | None = None
    cleaned_at: datetime | None = None
    schema_version: Literal[1] = 1

    @field_validator("worktree_path")
    @classmethod
    def _worktree_path_is_absolute(cls, value: Path) -> Path:
        if not value.is_absolute() or len(str(value)) > 4_096:
            raise ValueError("ownership worktree paths must be absolute")
        return value

    @field_validator("created_at", "cleaned_at")
    @classmethod
    def _timestamps_are_utc(cls, value: datetime | None) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value)
        ):
            raise ValueError("ownership timestamps must be timezone-aware UTC")
        return value

    @model_validator(mode="after")
    def _lifecycle_fields_are_consistent(self) -> Self:
        if self.lifecycle_status is WorktreeLifecycleStatus.PARTIAL:
            if self.last_error_category is None:
                raise ValueError("partial ownership records require an error category")
        elif self.last_error_category is not None:
            raise ValueError("only partial ownership records carry an error category")
        cleaned = self.lifecycle_status is WorktreeLifecycleStatus.REMOVED
        if cleaned != (self.cleaned_at is not None and self.cleanup_head is not None):
            raise ValueError("removed ownership records require complete cleanup evidence")
        return self


class WorktreeCreationResult(_GitModel):
    status: Literal[GitOperationStatus.CREATED]
    record: WorktreeOwnershipRecord
    worktree: WorktreeSnapshot


class WorktreeVerificationResult(_GitModel):
    status: Literal[GitOperationStatus.VERIFIED]
    record: WorktreeOwnershipRecord
    worktree: WorktreeSnapshot
    repository: RepositorySnapshot


class WorktreeCleanupResult(_GitModel):
    status: Literal[GitOperationStatus.REMOVED, GitOperationStatus.ALREADY_REMOVED]
    record: WorktreeOwnershipRecord


class GitRepository(Protocol):
    """Application-facing repository inspection and owned-worktree port."""

    def discover(self, path: Path) -> RepositoryIdentity: ...

    def inspect(self, path: Path) -> RepositorySnapshot: ...

    def create_worktree(self, request: WorktreeCreationRequest) -> WorktreeCreationResult: ...

    def verify_owned_worktree(self, worktree_id: WorktreeId) -> WorktreeVerificationResult: ...

    def cleanup_worktree(self, worktree_id: WorktreeId) -> WorktreeCleanupResult: ...
