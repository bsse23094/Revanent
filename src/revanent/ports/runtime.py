"""Durable runtime identity evidence used by user-facing workflow operations."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from revanent.domain import Run, RunId
from revanent.ports.git import GitRepository, RepositoryIdentity, WorktreeId
from revanent.ports.orchestration import OrchestrationJournal
from revanent.ports.storage import RunRepository, StoredRun
from revanent.ports.telemetry import TelemetryRepository

RUNTIME_BINDING_SCHEMA_VERSION: Literal[1] = 1


class RuntimeBinding(BaseModel):
    """Immutable repository/worktree correlation persisted with a newly created Run."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, str_strip_whitespace=True)

    schema_version: Literal[1] = RUNTIME_BINDING_SCHEMA_VERSION
    run_id: RunId
    repository: RepositoryIdentity
    worktree_id: WorktreeId
    worktree_relative_path: str = Field(pattern=r"^[A-Za-z0-9.][A-Za-z0-9._/-]{0,511}$")
    branch_name: str = Field(min_length=1, max_length=255)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("runtime binding timestamp must be UTC")
        return value

    @model_validator(mode="after")
    def _safe_relative_worktree(self) -> Self:
        path = Path(self.worktree_relative_path)
        if path.is_absolute() or ".." in path.parts or "\\" in self.worktree_relative_path:
            raise ValueError("runtime worktree reference must be normalized and relative")
        if self.worktree_id.root != "wt_" + self.run_id.root.removeprefix("run_"):
            raise ValueError("runtime worktree identity must derive from the Run ID")
        return self


class RuntimeRepository(RunRepository, OrchestrationJournal, TelemetryRepository, Protocol):
    """Persistence needed by runtime commands and their read-only projection."""

    def create_bound_run(self, run: Run, binding: RuntimeBinding) -> StoredRun: ...

    def get_runtime_binding(self, run_id: RunId) -> RuntimeBinding: ...


class RuntimeIdentityPort(Protocol):
    """Read-only live repository/worktree verification used before runtime mutation."""

    @property
    def repository_root(self) -> Path: ...

    @property
    def repository(self) -> GitRepository: ...

    def current_identity(self) -> RepositoryIdentity: ...
