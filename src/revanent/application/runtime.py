"""Safe local composition for P6 read-only application services."""

from __future__ import annotations

import os
from pathlib import Path

from revanent.commands import (
    CommandPolicy,
    EnvironmentPolicy,
    ExecutablePolicy,
    LocalCommandRunner,
    PathPolicy,
)
from revanent.git import LocalGitRepository, WorktreeOwnershipStore
from revanent.ports import CommandRunner, GitError, RepositorySnapshot

_BASELINE_NAMES = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "LANG",
    "LC_ALL",
)
_GIT_ENVIRONMENT_KEYS = frozenset(
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


class RepositoryInspectionError(Exception):
    """A safe category/message projection of the existing Git inspection boundary."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


def controlled_host_runner(
    repository_root: Path,
    executables: tuple[str, ...],
    *,
    allow_provider_stdin: bool = False,
) -> CommandRunner:
    """Compose a runner from selected host variables and exclude repository executables."""
    search_path = os.environ.get("PATH", "")
    try:
        executable_policy = ExecutablePolicy.from_search_path(
            executables,
            search_path,
            windows_extensions=(".exe", ".com", ".cmd", ".bat"),
            excluded_roots=(repository_root,),
        )
    except ValueError as error:
        raise RepositoryInspectionError(
            "PATH_UNAVAILABLE", "no usable configured search path"
        ) from error
    baseline = {name: os.environ[name] for name in _BASELINE_NAMES if name in os.environ}
    try:
        return LocalCommandRunner(
            executable_policy=executable_policy,
            path_policy=PathPolicy(_inspection_roots(repository_root)),
            environment_policy=EnvironmentPolicy(
                baseline,
                allowed_override_keys=_GIT_ENVIRONMENT_KEYS
                if "git" in executables
                else frozenset(),
            ),
            command_policy=CommandPolicy(allow_stdin=allow_provider_stdin),
        )
    except ValueError as error:
        raise RepositoryInspectionError(
            "RUNTIME_POLICY", "safe local command policy could not be constructed"
        ) from error


def inspect_repository(repository_path: Path) -> RepositorySnapshot:
    """Use the existing Git port for non-mutating repository-root discovery."""
    try:
        requested = repository_path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise RepositoryInspectionError(
            "PATH_UNAVAILABLE", "repository path is unavailable"
        ) from error
    if not requested.is_dir():
        raise RepositoryInspectionError("PATH_INVALID", "repository path must be a directory")
    runner = controlled_host_runner(requested, ("git",))
    try:
        repository = LocalGitRepository(
            runner=runner,
            path_policy=PathPolicy(_inspection_roots(requested)),
            worktree_root=requested,
            ownership_store=WorktreeOwnershipStore(requested),
        )
        return repository.inspect(requested)
    except GitError as error:
        raise RepositoryInspectionError(error.category.value, str(error)) from error
    except ValueError as error:
        raise RepositoryInspectionError(
            "RUNTIME_POLICY", "repository inspection policy rejected"
        ) from error


def repository_path_is_ignored(repository_root: Path, relative_path: Path) -> bool:
    """Ask the existing Git port whether an in-repository owned root is already ignored."""
    runner = controlled_host_runner(repository_root, ("git",))
    try:
        repository = LocalGitRepository(
            runner=runner,
            path_policy=PathPolicy(_inspection_roots(repository_root)),
            worktree_root=repository_root,
            ownership_store=WorktreeOwnershipStore(repository_root),
        )
        return repository.is_path_ignored(repository_root, relative_path)
    except GitError as error:
        raise RepositoryInspectionError(error.category.value, str(error)) from error
    except ValueError as error:
        raise RepositoryInspectionError(
            "RUNTIME_POLICY", "ignored-path inspection was rejected"
        ) from error


def _inspection_roots(path: Path) -> tuple[Path, ...]:
    """Permit Git to return the enclosing worktree root without authorizing a volume root."""
    roots: list[Path] = []
    for candidate in (path, *path.parents):
        if candidate == Path(candidate.anchor):
            break
        if candidate not in roots:
            roots.append(candidate)
    if not roots:
        raise RepositoryInspectionError(
            "PATH_INVALID", "repository path cannot be a filesystem root"
        )
    return tuple(roots)
