"""Bounded, non-authoritative task-file loading for the runtime CLI."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from pydantic import ValidationError

from revanent.domain import TaskSpecification

MAX_TASK_FILE_BYTES = 64 * 1_024


class TaskInputError(Exception):
    """Sanitized task-file refusal suitable for a CLI result."""


def load_task_file(repository_root: Path, path: Path) -> TaskSpecification:
    try:
        root = repository_root.resolve(strict=True)
        if path.is_absolute() or ".." in path.parts or path in {Path(""), Path(".")}:
            raise OSError("task path must be normalized and relative")
        candidate = root / path
        current = root
        for part in path.parts:
            current = current / part
            metadata = current.lstat()
            attributes = getattr(metadata, "st_file_attributes", 0)
            reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            if stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse):
                raise OSError("task path cannot use a link or junction")
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
        before = resolved.lstat()
        if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode):
            raise OSError("task file is not regular")
        if before.st_size > MAX_TASK_FILE_BYTES:
            raise OSError("task file is too large")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(resolved, flags)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_size > MAX_TASK_FILE_BYTES:
                raise OSError("task file changed")
            data = os.read(descriptor, MAX_TASK_FILE_BYTES + 1)
        finally:
            os.close(descriptor)
        after = resolved.lstat()
        if len(data) > MAX_TASK_FILE_BYTES or (
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise OSError("task file changed")
        document = json.loads(data.decode("utf-8"))
        return TaskSpecification.model_validate(document)
    except (OSError, RuntimeError, UnicodeError, json.JSONDecodeError, ValidationError) as error:
        raise TaskInputError(
            "task file must be a bounded valid task JSON below the repository root"
        ) from error
