"""Conservative, idempotent repository initialization without overwrite semantics."""

from __future__ import annotations

import os
import stat
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

from revanent.application.runtime import (
    RepositoryInspectionError,
    inspect_repository,
    repository_path_is_ignored,
)
from revanent.config import CONFIGURATION_FILENAME, render_default_config


class InitializationAction(StrEnum):
    CREATE = "CREATE"
    REUSE = "REUSE"
    REFUSE = "REFUSE"


@dataclass(frozen=True, slots=True)
class InitializationResource:
    relative_path: str
    action: InitializationAction
    reason: str


@dataclass(frozen=True, slots=True)
class InitializationPlan:
    repository_root: Path
    resources: tuple[InitializationResource, ...]
    configuration: bytes

    @property
    def allowed(self) -> bool:
        return all(
            resource.action is not InitializationAction.REFUSE for resource in self.resources
        )


@dataclass(frozen=True, slots=True)
class InitializationResult:
    succeeded: bool
    code: str
    message: str
    changed: bool
    plan: InitializationPlan | None = None


class InitializationService:
    """Construct a side-effect-free plan, then apply only its no-clobber actions."""

    def plan(self, repository_path: Path) -> InitializationResult:
        try:
            snapshot = inspect_repository(repository_path)
        except RepositoryInspectionError as error:
            return InitializationResult(False, error.category, str(error), False)
        root = snapshot.identity.worktree_root
        configuration = render_default_config(root.name or "revanent-project")
        if snapshot.status.operation_in_progress or (
            snapshot.status.has_changes and not _only_configuration_is_untracked(snapshot)
        ):
            return InitializationResult(
                False,
                "REPOSITORY_UNSAFE",
                "initialization requires a clean repository with no active Git operation",
                False,
            )
        try:
            return self._plan_for_root(root, configuration)
        except InitializationSafetyError as error:
            return InitializationResult(False, error.code, str(error), False)

    def initialize(self, repository_path: Path) -> InitializationResult:
        planned = self.plan(repository_path)
        if not planned.succeeded or planned.plan is None:
            return planned
        plan = planned.plan
        if not plan.allowed:
            return InitializationResult(
                False,
                "INITIALIZATION_CONFLICT",
                "initialization refused because an existing path is incompatible",
                False,
                plan,
            )
        changed = False
        try:
            for resource in _directory_resources(plan):
                if resource.action is InitializationAction.CREATE:
                    _create_directory(plan.repository_root / resource.relative_path)
                    changed = True
            config_resource = next(
                resource
                for resource in plan.resources
                if resource.relative_path == CONFIGURATION_FILENAME
            )
            if config_resource.action is InitializationAction.CREATE:
                _create_file_exclusive(
                    plan.repository_root / CONFIGURATION_FILENAME, plan.configuration
                )
                changed = True
        except InitializationSafetyError as error:
            return InitializationResult(False, error.code, str(error), changed, plan)
        return InitializationResult(
            True, "initialized", "initialization completed safely", changed, plan
        )

    def _plan_for_root(
        self, root: Path, configuration: bytes | None = None
    ) -> InitializationResult:
        configuration = configuration or render_default_config(root.name or "revanent-project")
        config_path = _safe_child(root, Path(CONFIGURATION_FILENAME))
        workspace = _safe_child(root, Path(".revanent/worktrees"))
        reports = _safe_child(root, Path(".revanent/runs"))
        state = _safe_child(root, Path(".revanent/state"))
        owned_root = _safe_child(root, Path(".revanent"))
        _require_distinct((workspace, reports, state))
        try:
            ignored = repository_path_is_ignored(root, Path(".revanent"))
        except RepositoryInspectionError as error:
            return InitializationResult(False, error.category, str(error), False)
        if not ignored:
            return InitializationResult(
                False,
                "OWNED_ROOT_NOT_IGNORED",
                "the in-repository .revanent root must already be ignored; "
                "initialization does not edit .gitignore",
                False,
            )
        resources: tuple[InitializationResource, ...] = (
            _file_resource(root, config_path, configuration),
            _directory_resource(root, owned_root),
            _directory_resource(root, workspace),
            _directory_resource(root, reports),
            _directory_resource(root, state),
        )
        if owned_root.exists() and not _owned_root_is_compatible(owned_root):
            resources = tuple(
                InitializationResource(
                    resource.relative_path,
                    InitializationAction.REFUSE,
                    "the Revanent-owned root has unexpected entries",
                )
                if resource.relative_path == ".revanent"
                else resource
                for resource in resources
            )
        plan = InitializationPlan(root, resources, configuration)
        return InitializationResult(True, "planned", "initialization plan is valid", False, plan)


class InitializationSafetyError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _safe_child(root: Path, relative: Path) -> Path:
    if relative.is_absolute() or ".." in PurePosixPath(str(relative).replace("\\", "/")).parts:
        raise InitializationSafetyError(
            "PATH_INVALID", "initialization path must be repository-relative"
        )
    target = root / relative
    current = root
    for part in relative.parts:
        current = current / part
        if os.path.lexists(current):
            try:
                metadata = current.lstat()
            except OSError as error:
                raise InitializationSafetyError(
                    "PATH_UNAVAILABLE", "initialization path is unavailable"
                ) from error
            attributes = getattr(metadata, "st_file_attributes", 0)
            reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            if stat.S_ISLNK(metadata.st_mode) or attributes & reparse:
                raise InitializationSafetyError(
                    "PATH_LINK_REFUSED", "initialization refuses symbolic links or junctions"
                )
    try:
        normalized = target.resolve(strict=False)
        normalized.relative_to(root)
    except (OSError, RuntimeError, ValueError) as error:
        raise InitializationSafetyError(
            "PATH_ESCAPE", "initialization path escapes the repository"
        ) from error
    if normalized.parts[len(root.parts) : len(root.parts) + 1] == (".git",):
        raise InitializationSafetyError(
            "PATH_GIT_REFUSED", "initialization paths cannot be inside .git"
        )
    return target


def _require_distinct(paths: tuple[Path, ...]) -> None:
    normalized = {os.path.normcase(str(path.resolve(strict=False))) for path in paths}
    if len(normalized) != len(paths):
        raise InitializationSafetyError(
            "PATH_COLLISION", "workspace, report, and state roots must be distinct"
        )


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _file_resource(root: Path, path: Path, expected: bytes) -> InitializationResource:
    relative = _relative(root, path)
    if not os.path.lexists(path):
        return InitializationResource(
            relative, InitializationAction.CREATE, "configuration will be created"
        )
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise OSError("not a regular file")
        if metadata.st_size > 256 * 1_024:
            raise OSError("configuration exceeds the size limit")
        current = path.read_bytes()
    except OSError:
        return InitializationResource(
            relative, InitializationAction.REFUSE, "existing configuration is unsafe"
        )
    if current == expected:
        return InitializationResource(
            relative, InitializationAction.REUSE, "existing configuration is identical"
        )
    return InitializationResource(
        relative, InitializationAction.REFUSE, "existing configuration differs"
    )


def _directory_resource(root: Path, path: Path) -> InitializationResource:
    relative = _relative(root, path)
    if not os.path.lexists(path):
        return InitializationResource(
            relative, InitializationAction.CREATE, "directory will be created"
        )
    try:
        metadata = path.lstat()
    except OSError:
        return InitializationResource(
            relative, InitializationAction.REFUSE, "existing directory is unavailable"
        )
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        return InitializationResource(
            relative, InitializationAction.REFUSE, "existing path is not a safe directory"
        )
    return InitializationResource(
        relative, InitializationAction.REUSE, "existing compatible directory will be reused"
    )


def _owned_root_is_compatible(path: Path) -> bool:
    try:
        names = {entry.name for entry in path.iterdir()}
    except OSError:
        return False
    return names <= {"worktrees", "runs", "state"}


def _directory_resources(plan: InitializationPlan) -> tuple[InitializationResource, ...]:
    return tuple(
        sorted(
            (
                resource
                for resource in plan.resources
                if resource.relative_path != CONFIGURATION_FILENAME
            ),
            key=lambda resource: (len(Path(resource.relative_path).parts), resource.relative_path),
        )
    )


def _create_directory(path: Path) -> None:
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        resource = _directory_resource(path.parent, path)
        if resource.action is not InitializationAction.REUSE:
            raise InitializationSafetyError(
                "INITIALIZATION_CONFLICT", "concurrent directory conflict"
            ) from None
    except OSError as error:
        raise InitializationSafetyError(
            "INITIALIZATION_WRITE_FAILED", "could not create owned directory"
        ) from error


def _create_file_exclusive(path: Path, data: bytes) -> None:
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".revanent-init-", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    except FileExistsError as error:
        raise InitializationSafetyError(
            "INITIALIZATION_CONFLICT", "configuration appeared concurrently"
        ) from error
    except OSError as error:
        raise InitializationSafetyError(
            "INITIALIZATION_WRITE_FAILED", "could not create configuration safely"
        ) from error
    finally:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)


def _only_configuration_is_untracked(snapshot: object) -> bool:
    """Permit init to report a root configuration conflict without accepting other source dirt."""
    status = getattr(snapshot, "status", None)
    if status is None:
        return False
    if any(
        getattr(status, name, ()) for name in ("staged_paths", "unstaged_paths", "conflicted_paths")
    ):
        return False
    return tuple(getattr(status, "untracked_paths", ())) == (CONFIGURATION_FILENAME,)
