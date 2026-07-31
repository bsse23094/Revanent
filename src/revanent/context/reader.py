"""Single bounded filesystem-reading boundary for context construction."""

from __future__ import annotations

import stat
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from revanent.ports.agents import RepositoryPath


class ContextReadStatus(StrEnum):
    COMPLETE = "COMPLETE"
    MISSING = "MISSING"
    ESCAPE = "ESCAPE"
    SPECIAL = "SPECIAL"
    CHANGED = "CHANGED"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class ContextReadResult:
    status: ContextReadStatus
    content: bytes = b""
    observed_bytes: int | None = None


class ContextFileReaderPort(Protocol):
    def read(
        self,
        *,
        root: Path,
        path: RepositoryPath,
        max_bytes: int,
    ) -> ContextReadResult: ...

    def find_named_files(
        self,
        *,
        root: Path,
        search_roots: tuple[RepositoryPath, ...],
        names: tuple[str, ...],
        max_entries: int,
    ) -> tuple[RepositoryPath, ...]: ...


class LocalContextFileReader:
    """Read one relative regular file with before/after consistency evidence."""

    def read(
        self,
        *,
        root: Path,
        path: RepositoryPath,
        max_bytes: int,
    ) -> ContextReadResult:
        try:
            approved_root = root.resolve(strict=True)
            target = approved_root.joinpath(*path.root.split("/"))
            before_resolved = target.resolve(strict=True)
            if not before_resolved.is_relative_to(approved_root):
                return ContextReadResult(ContextReadStatus.ESCAPE)
            before = before_resolved.stat(follow_symlinks=False)
            if not stat.S_ISREG(before.st_mode):
                return ContextReadResult(
                    ContextReadStatus.SPECIAL,
                    observed_bytes=before.st_size,
                )
            with before_resolved.open("rb") as stream:
                opened_before = stat_result_identity(stream.fileno())
                content = stream.read(max_bytes + 1)
                opened_after = stat_result_identity(stream.fileno())
            after_resolved = target.resolve(strict=True)
            after = after_resolved.stat(follow_symlinks=False)
        except FileNotFoundError:
            return ContextReadResult(ContextReadStatus.MISSING)
        except OSError:
            return ContextReadResult(ContextReadStatus.ERROR)
        if (
            after_resolved != before_resolved
            or _metadata_identity(before) != _metadata_identity(after)
            or opened_before != opened_after
            or opened_before != _metadata_identity(before)
        ):
            return ContextReadResult(
                ContextReadStatus.CHANGED,
                observed_bytes=max(before.st_size, after.st_size),
            )
        return ContextReadResult(
            ContextReadStatus.COMPLETE,
            content=content,
            observed_bytes=before.st_size,
        )

    def find_named_files(
        self,
        *,
        root: Path,
        search_roots: tuple[RepositoryPath, ...],
        names: tuple[str, ...],
        max_entries: int,
    ) -> tuple[RepositoryPath, ...]:
        approved_root = root.resolve(strict=True)
        wanted = frozenset(names)
        matches: list[RepositoryPath] = []
        visited = 0
        pending = sorted(
            (approved_root.joinpath(*item.root.split("/")) for item in search_roots),
            key=lambda item: item.as_posix(),
            reverse=True,
        )
        while pending and visited < max_entries:
            directory = pending.pop()
            try:
                resolved = directory.resolve(strict=True)
                if not resolved.is_relative_to(approved_root):
                    continue
                entries = sorted(resolved.iterdir(), key=lambda item: item.name.casefold())
            except (FileNotFoundError, NotADirectoryError, OSError):
                continue
            for entry in entries:
                visited += 1
                if visited > max_entries:
                    break
                try:
                    entry_resolved = entry.resolve(strict=True)
                    if not entry_resolved.is_relative_to(approved_root) or entry.is_symlink():
                        continue
                    if entry.is_dir():
                        if entry.name.casefold() not in _EXCLUDED_DIRECTORY_NAMES:
                            pending.append(entry)
                    elif entry.is_file() and entry.name in wanted:
                        relative = entry_resolved.relative_to(approved_root).as_posix()
                        matches.append(RepositoryPath(relative))
                except OSError:
                    continue
        return tuple(sorted(set(matches), key=lambda item: item.root))


_EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".revanent",
        ".venv",
        "venv",
        "node_modules",
        "build",
        "dist",
        ".pytest_cache",
        "__pycache__",
    }
)


def stat_result_identity(file_descriptor: int) -> tuple[int, int, int, int]:
    import os

    return _metadata_identity(os.fstat(file_descriptor))


def _metadata_identity(value: object) -> tuple[int, int, int, int]:
    return (
        int(getattr(value, "st_dev", 0)),
        int(getattr(value, "st_ino", 0)),
        int(getattr(value, "st_size", 0)),
        int(getattr(value, "st_mtime_ns", 0)),
    )
