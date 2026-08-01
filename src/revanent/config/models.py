"""Strict version-1 Revanent project configuration."""

from __future__ import annotations

from decimal import Decimal
from pathlib import PurePath, PurePosixPath, PureWindowsPath
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

NonEmpty = Annotated[str, Field(min_length=1, max_length=256)]
RelativePath = Annotated[str, Field(min_length=1, max_length=512)]


class _ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


def _validate_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    windows = PureWindowsPath(normalized)
    posix = PurePosixPath(normalized)
    if windows.is_absolute() or windows.drive or posix.is_absolute():
        raise ValueError("path must be relative to the target repository")
    if ".." in posix.parts:
        raise ValueError("path cannot traverse above the target repository")
    if normalized in {"", "."}:
        raise ValueError("path must name a repository-relative location")
    return normalized


def _validate_path_pattern(value: str) -> str:
    normalized = _validate_relative_path(value)
    if normalized in {"*", "**", "**/*"}:
        raise ValueError("an allowed path pattern cannot grant repository-wide access")
    return normalized


class ProjectConfig(_ConfigModel):
    name: NonEmpty


class WorkspaceConfig(_ConfigModel):
    strategy: Literal["git-worktree"] = "git-worktree"
    root: RelativePath = ".revanent/worktrees"
    preserve_failed: bool = True

    _root_is_relative = field_validator("root")(_validate_relative_path)


class BuilderConfig(_ConfigModel):
    provider: Literal["opencode"] = "opencode"
    model: NonEmpty
    max_attempts: int = Field(default=3, ge=1, le=100)
    timeout_seconds: int = Field(default=1_800, ge=1, le=86_400)


class ReviewerConfig(_ConfigModel):
    provider: Literal["codex"] = "codex"
    mode: Literal["review_only", "review_then_repair"] = "review_then_repair"
    max_reviews: int = Field(default=3, ge=1, le=100)
    max_repairs: int = Field(default=2, ge=0, le=100)
    timeout_seconds: int = Field(default=1_800, ge=1, le=86_400)

    @model_validator(mode="after")
    def _validate_repairs(self) -> Self:
        if self.mode == "review_only" and self.max_repairs != 0:
            raise ValueError("review_only mode requires max_repairs=0")
        return self


class ValidationCommandConfig(_ConfigModel):
    name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
    command: tuple[NonEmpty, ...]

    @field_validator("command")
    @classmethod
    def _command_is_not_empty(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("validation command must contain an executable")
        return value


class ValidationConfig(_ConfigModel):
    commands: tuple[ValidationCommandConfig, ...]

    @field_validator("commands")
    @classmethod
    def _commands_are_unique(
        cls, value: tuple[ValidationCommandConfig, ...]
    ) -> tuple[ValidationCommandConfig, ...]:
        if not value:
            raise ValueError("at least one validation command is required")
        names = [command.name for command in value]
        if len(names) != len(set(names)):
            raise ValueError("validation command names must be unique")
        return value


class BudgetsConfig(_ConfigModel):
    max_total_minutes: int = Field(default=90, ge=1, le=10_080)
    max_remote_tokens: int | None = Field(default=None, ge=1)
    max_estimated_cost_usd: Decimal | None = Field(default=None, gt=0, max_digits=12)


class PolicyConfig(_ConfigModel):
    allowed_paths: tuple[RelativePath, ...]
    forbidden_paths: tuple[RelativePath, ...] = (".git/**", ".env")
    allow_codex_write_repair: bool = False
    allow_live_opencode_builder: bool = False
    allow_live_codex_reviewer: bool = False
    allow_network: bool = False
    allow_push: Literal[False] = False
    allow_merge: Literal[False] = False

    @field_validator("allowed_paths")
    @classmethod
    def _allowed_paths_are_safe(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_validate_path_pattern(item) for item in value)

    @field_validator("forbidden_paths")
    @classmethod
    def _forbidden_paths_are_relative(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_validate_relative_path(item) for item in value)

    @model_validator(mode="after")
    def _validate_scope(self) -> Self:
        if not self.allowed_paths:
            raise ValueError("at least one allowed path is required")
        if len(self.allowed_paths) != len(set(self.allowed_paths)):
            raise ValueError("allowed paths must be unique")
        if len(self.forbidden_paths) != len(set(self.forbidden_paths)):
            raise ValueError("forbidden paths must be unique")
        overlap = set(self.allowed_paths) & set(self.forbidden_paths)
        if overlap:
            raise ValueError(f"paths cannot be both allowed and forbidden: {sorted(overlap)!r}")
        return self


class ReportingConfig(_ConfigModel):
    directory: RelativePath = ".revanent/runs"
    formats: tuple[Literal["json", "markdown"], ...] = ("json", "markdown")

    _directory_is_relative = field_validator("directory")(_validate_relative_path)

    @field_validator("formats")
    @classmethod
    def _formats_are_unique(
        cls, value: tuple[Literal["json", "markdown"], ...]
    ) -> tuple[Literal["json", "markdown"], ...]:
        if not value:
            raise ValueError("at least one reporting format is required")
        if len(value) != len(set(value)):
            raise ValueError("reporting formats must be unique")
        return value


class RevanentConfig(_ConfigModel):
    """The complete accepted project configuration for schema version 1."""

    schema_version: Literal[1]
    project: ProjectConfig
    workspace: WorkspaceConfig
    builder: BuilderConfig
    reviewer: ReviewerConfig
    validation: ValidationConfig
    budgets: BudgetsConfig = Field(default_factory=BudgetsConfig)
    policy: PolicyConfig
    reporting: ReportingConfig = Field(default_factory=ReportingConfig)

    @model_validator(mode="after")
    def _validate_internal_paths(self) -> Self:
        workspace = PurePath(self.workspace.root)
        reports = PurePath(self.reporting.directory)
        if workspace == reports:
            raise ValueError("workspace root and reporting directory must be distinct")
        for label, path in (("workspace root", workspace), ("reporting directory", reports)):
            if any(part.casefold() == ".git" for part in path.parts):
                raise ValueError(f"{label} cannot be located under .git")
        return self
