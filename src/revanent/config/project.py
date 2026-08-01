"""Repository-bound configuration discovery and effective-config projection."""

from __future__ import annotations

import stat
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import yaml

from revanent.config.errors import ConfigurationError, ConfigurationValidationError
from revanent.config.loader import load_config
from revanent.config.models import RevanentConfig

CONFIGURATION_FILENAME = "revanent.yaml"


class ConfigurationSource(StrEnum):
    """The accepted non-secret sources for one effective setting."""

    DEFAULT = "default"
    PROJECT = "project"
    CLI = "cli"


class ProjectConfigurationError(ConfigurationError):
    """Repository-bound configuration discovery or override failure."""


@dataclass(frozen=True, slots=True)
class EffectiveConfiguration:
    """Validated configuration with bounded, non-secret source provenance."""

    repository_root: Path
    path: Path
    config: RevanentConfig
    max_total_minutes_source: ConfigurationSource


@dataclass(frozen=True, slots=True)
class ProjectPaths:
    """Resolved schema-v1 owned roots, all contained under one repository root."""

    workspace_root: Path
    report_root: Path
    state_root: Path


def normalize_repository_root(path: Path) -> Path:
    """Return one existing directory identity without accepting a link target escape later."""
    try:
        root = path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ProjectConfigurationError("repository path is unavailable") from error
    if not root.is_dir():
        raise ProjectConfigurationError("repository path must be an existing directory")
    return root


def configuration_path(repository_root: Path, explicit_path: Path | None = None) -> Path:
    """Resolve only the accepted root-level configuration filename."""
    root = normalize_repository_root(repository_root)
    expected = root / CONFIGURATION_FILENAME
    if explicit_path is None:
        return expected
    candidate = explicit_path if explicit_path.is_absolute() else root / explicit_path
    try:
        normalized = candidate.resolve(strict=False)
    except (OSError, RuntimeError) as error:
        raise ProjectConfigurationError("configuration path is unavailable") from error
    if normalized != expected:
        raise ProjectConfigurationError(
            f"configuration must be the repository-root {CONFIGURATION_FILENAME} file"
        )
    return expected


def default_config(project_name: str) -> RevanentConfig:
    """Build the one canonical, safe schema-version-1 initialization template."""
    document = {
        "schema_version": 1,
        "project": {"name": project_name},
        "workspace": {
            "strategy": "git-worktree",
            "root": ".revanent/worktrees",
            "preserve_failed": True,
        },
        "builder": {
            "provider": "opencode",
            "model": "configured-local-model",
            "max_attempts": 3,
            "timeout_seconds": 1_800,
        },
        "reviewer": {
            "provider": "codex",
            "mode": "review_then_repair",
            "max_reviews": 3,
            "max_repairs": 2,
            "timeout_seconds": 1_800,
        },
        "validation": {
            "commands": (
                {"name": "tests", "command": ("uv", "run", "pytest")},
                {"name": "lint", "command": ("uv", "run", "ruff", "check", ".")},
                {"name": "format", "command": ("uv", "run", "ruff", "format", "--check", ".")},
                {"name": "types", "command": ("uv", "run", "mypy", "src", "tests")},
            )
        },
        "budgets": {
            "max_total_minutes": 90,
            "max_remote_tokens": None,
            "max_estimated_cost_usd": None,
        },
        "policy": {
            "allowed_paths": ("src/**", "tests/**", "docs/**"),
            "forbidden_paths": (".git/**", ".env", "**/secrets/**"),
            "allow_codex_write_repair": True,
            "allow_live_opencode_builder": False,
            "allow_live_codex_reviewer": False,
            "allow_network": False,
            "allow_push": False,
            "allow_merge": False,
        },
        "reporting": {"directory": ".revanent/runs", "formats": ("json", "markdown")},
    }
    return RevanentConfig.model_validate(document)


def render_default_config(project_name: str) -> bytes:
    """Render the validated canonical template deterministically and without secrets."""
    config = default_config(project_name)
    document = config.model_dump(mode="json")
    rendered = yaml.safe_dump(document, allow_unicode=True, sort_keys=False)
    return rendered.encode("utf-8")


def load_effective_config(
    repository_root: Path,
    *,
    explicit_path: Path | None = None,
    max_total_minutes: int | None = None,
    environment: Mapping[str, str] | None = None,
) -> EffectiveConfiguration:
    """Load a project config then apply the sole reviewed non-secret CLI override.

    Schema v1 intentionally declares no secret-reference field, so ``environment`` is
    accepted only to make the absence of a general environment overlay explicit.
    """
    del environment
    root = normalize_repository_root(repository_root)
    path = configuration_path(root, explicit_path)
    config = load_config(path)
    source = ConfigurationSource.PROJECT
    if max_total_minutes is not None:
        if type(max_total_minutes) is not int:
            raise ProjectConfigurationError("max-total-minutes override must be an integer")
        document = config.model_dump(mode="python")
        budgets = document["budgets"]
        assert isinstance(budgets, dict)
        budgets["max_total_minutes"] = max_total_minutes
        try:
            config = RevanentConfig.model_validate(document)
        except Exception as error:
            raise ConfigurationValidationError(
                "budgets.max_total_minutes: override is invalid"
            ) from error
        source = ConfigurationSource.CLI
    return EffectiveConfiguration(
        repository_root=root,
        path=path,
        config=config,
        max_total_minutes_source=source,
    )


def resolve_project_paths(effective: EffectiveConfiguration) -> ProjectPaths:
    """Resolve configured roots from the repository, refusing link and sibling-prefix escapes."""
    root = effective.repository_root
    workspace = _resolve_project_relative(root, effective.config.workspace.root)
    reports = _resolve_project_relative(root, effective.config.reporting.directory)
    state = _resolve_project_relative(root, ".revanent/state")
    normalized = {str(path).casefold() for path in (workspace, reports, state)}
    if len(normalized) != 3:
        raise ProjectConfigurationError("workspace, report, and state roots must be distinct")
    return ProjectPaths(workspace, reports, state)


def _resolve_project_relative(root: Path, value: str) -> Path:
    path = root / Path(value)
    current = root
    for part in Path(value).parts:
        current = current / part
        if _is_link_or_reparse(current):
            raise ProjectConfigurationError(
                "configured root cannot use a symbolic link or junction"
            )
    try:
        resolved = path.resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError) as error:
        raise ProjectConfigurationError("configured root escapes the target repository") from error
    return resolved


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse)
