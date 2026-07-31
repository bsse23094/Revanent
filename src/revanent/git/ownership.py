"""Atomic, versioned ownership records for Revanent-created worktrees."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import IO

from pydantic import ValidationError

from revanent.ports.git import (
    MalformedOwnershipRecordError,
    OwnershipRecordConflictError,
    UnownedWorktreeError,
    WorktreeId,
    WorktreeOwnershipRecord,
)

MAX_OWNERSHIP_RECORD_BYTES = 128 * 1_024


def _is_unc(path: Path) -> bool:
    return str(path).replace("/", "\\").startswith("\\\\")


class OwnershipLease:
    """Exclusive per-worktree lease used across one local lifecycle mutation."""

    def __init__(self, store: WorktreeOwnershipStore, worktree_id: WorktreeId) -> None:
        self._store = store
        self.worktree_id = worktree_id

    def exists(self) -> bool:
        return os.path.lexists(self._store._record_path(self.worktree_id))

    def load(self) -> WorktreeOwnershipRecord:
        return self._store._load_unlocked(self.worktree_id)

    def write(self, record: WorktreeOwnershipRecord, *, replace: bool) -> None:
        if record.worktree_id != self.worktree_id:
            raise OwnershipRecordConflictError(
                "ownership lease and record identifiers do not match",
                worktree_id=self.worktree_id,
            )
        self._store._write_unlocked(record, replace=replace)


class WorktreeOwnershipStore:
    """Dedicated bounded JSON store; records remain after verified cleanup."""

    def __init__(self, state_directory: Path, *, allow_unc: bool = False) -> None:
        if not isinstance(state_directory, Path) or not state_directory.is_absolute():
            raise ValueError("ownership state directory must be an absolute pathlib path")
        if _is_unc(state_directory) and not allow_unc:
            raise ValueError("ownership state on UNC paths requires explicit authorization")
        try:
            root = state_directory.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise ValueError("ownership state directory must already exist") from error
        if not root.is_dir() or root == Path(root.anchor):
            raise ValueError("ownership state must be a non-root directory")
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    @property
    def disabled_hooks_path(self) -> Path:
        """A deliberately absent owned path used to neutralize repository hooks."""
        return self._root / "disabled-hooks"

    def record_path(self, worktree_id: WorktreeId) -> Path:
        """Return the contained record location for diagnostics and tests."""
        return self._record_path(worktree_id)

    def load(self, worktree_id: WorktreeId) -> WorktreeOwnershipRecord:
        return self._load_unlocked(worktree_id)

    @contextmanager
    def acquire(self, worktree_id: WorktreeId) -> Iterator[OwnershipLease]:
        """Acquire an atomic create-exclusive lease; stale locks fail closed."""
        lock_path = self._lock_path(worktree_id)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        try:
            descriptor = os.open(lock_path, flags, 0o600)
        except FileExistsError as error:
            raise OwnershipRecordConflictError(
                "ownership record is locked by another or interrupted operation",
                worktree_id=worktree_id,
            ) from error
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write((str(worktree_id) + "\n").encode("ascii"))
                stream.flush()
                os.fsync(stream.fileno())
            yield OwnershipLease(self, worktree_id)
        finally:
            self._remove_owned_file(lock_path, expected_suffix=".lock")

    def _record_path(self, worktree_id: WorktreeId) -> Path:
        return self._contained_filename(f"{worktree_id}.json")

    def _lock_path(self, worktree_id: WorktreeId) -> Path:
        return self._contained_filename(f"{worktree_id}.lock")

    def _contained_filename(self, filename: str) -> Path:
        if Path(filename).name != filename or any(separator in filename for separator in "/\\"):
            raise ValueError("ownership filename is invalid")
        target = self._root / filename
        if target.parent != self._root:
            raise ValueError("ownership filename escapes the state directory")
        return target

    def _load_unlocked(self, worktree_id: WorktreeId) -> WorktreeOwnershipRecord:
        path = self._record_path(worktree_id)
        if not os.path.lexists(path):
            raise UnownedWorktreeError(
                "no Revanent ownership record exists for the worktree",
                worktree_id=worktree_id,
            )
        try:
            if path.is_symlink() or path.resolve(strict=True).parent != self._root:
                raise MalformedOwnershipRecordError(
                    "ownership record path does not resolve to the owned state directory",
                    worktree_id=worktree_id,
                )
            data = self._read_bounded(path)
            record = WorktreeOwnershipRecord.model_validate_json(data)
        except MalformedOwnershipRecordError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError, ValidationError, ValueError) as error:
            raise MalformedOwnershipRecordError(
                "ownership record is malformed or unsupported",
                worktree_id=worktree_id,
            ) from error
        if record.worktree_id != worktree_id:
            raise MalformedOwnershipRecordError(
                "ownership record identifier does not match its filename",
                worktree_id=worktree_id,
            )
        return record

    @staticmethod
    def _read_bounded(path: Path) -> bytes:
        flags = os.O_RDONLY
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_OWNERSHIP_RECORD_BYTES:
                raise MalformedOwnershipRecordError("ownership record is not a bounded file")
            chunks: list[bytes] = []
            remaining = MAX_OWNERSHIP_RECORD_BYTES + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(16 * 1_024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            if len(data) > MAX_OWNERSHIP_RECORD_BYTES:
                raise MalformedOwnershipRecordError("ownership record exceeds its size limit")
            return data
        finally:
            os.close(descriptor)

    def _write_unlocked(self, record: WorktreeOwnershipRecord, *, replace: bool) -> None:
        target = self._record_path(record.worktree_id)
        if os.path.lexists(target) != replace:
            raise OwnershipRecordConflictError(
                "ownership record existence does not match the requested lifecycle update",
                worktree_id=record.worktree_id,
            )
        payload = (
            json.dumps(
                record.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        if len(payload) > MAX_OWNERSHIP_RECORD_BYTES:
            raise MalformedOwnershipRecordError(
                "ownership record exceeds its size limit",
                worktree_id=record.worktree_id,
            )
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{record.worktree_id}.",
            suffix=".tmp",
            dir=self._root,
        )
        temporary = Path(temporary_name)
        try:
            self._write_and_sync(descriptor, payload)
            if os.path.lexists(target) != replace:
                raise OwnershipRecordConflictError(
                    "ownership record changed concurrently",
                    worktree_id=record.worktree_id,
                )
            os.replace(temporary, target)
            self._sync_directory()
        finally:
            if os.path.lexists(temporary):
                self._remove_owned_file(temporary, expected_suffix=".tmp")

    @staticmethod
    def _write_and_sync(descriptor: int, payload: bytes) -> None:
        stream: IO[bytes]
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())

    def _sync_directory(self) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(self._root, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _remove_owned_file(self, path: Path, *, expected_suffix: str) -> None:
        if path.parent != self._root or path.suffix != expected_suffix:
            raise RuntimeError("refusing to remove a file outside the ownership state directory")
        with suppress(FileNotFoundError):
            path.unlink()
