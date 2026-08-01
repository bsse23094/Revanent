"""No-clobber atomic writer for explicit evidence-report artifacts."""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from contextlib import suppress
from pathlib import Path

from revanent.ports.reporting import ReportArtifact, ReportArtifactWriteResult


class ReportArtifactWriteError(ValueError):
    """The requested report output path or content cannot be safely written."""


class LocalReportArtifactWriter:
    """Write only a report-root-relative file with create-exclusive finalization."""

    def write(
        self,
        *,
        root: Path,
        relative_path: str,
        data: bytes,
        content_type: str,
        correlation: str,
    ) -> ReportArtifactWriteResult:
        target = self._target(root, relative_path)
        self._ensure_parent(root, target.parent)
        digest = hashlib.sha256(data).hexdigest()
        artifact = ReportArtifact(
            reference=relative_path.replace("\\", "/"),
            content_type=content_type,
            observed_bytes=len(data),
            stored_bytes=len(data),
            digest_sha256=digest,
            complete=True,
            correlation=correlation,
        )
        if target.exists() or target.is_symlink():
            return self._existing(target, data, artifact)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
        )
        temporary = Path(temporary_name)
        try:
            with suppress(OSError):
                os.chmod(temporary, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                return self._existing(target, data, artifact)
            return ReportArtifactWriteResult(artifact=artifact, created=True)
        finally:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)

    def _target(self, root: Path, relative_path: str) -> Path:
        if not relative_path or len(relative_path.encode("utf-8")) > 512:
            raise ReportArtifactWriteError("report output name is invalid")
        value = Path(relative_path)
        if value.is_absolute() or ".." in value.parts or value in {Path("."), Path("")}:
            raise ReportArtifactWriteError("report output must be relative to the report root")
        if any(part.casefold() == ".git" for part in value.parts):
            raise ReportArtifactWriteError("report output cannot target .git")
        resolved_root = root.resolve(strict=True)
        if not resolved_root.is_dir() or self._linked(resolved_root):
            raise ReportArtifactWriteError("report root is unavailable or unsafe")
        target = resolved_root / value
        if target.parent != resolved_root and not target.parent.is_relative_to(resolved_root):
            raise ReportArtifactWriteError("report output escapes the report root")
        return target

    def _ensure_parent(self, root: Path, parent: Path) -> None:
        relative = parent.relative_to(root.resolve(strict=True))
        current = root.resolve(strict=True)
        for part in relative.parts:
            current = current / part
            if current.exists():
                if self._linked(current) or not current.is_dir():
                    raise ReportArtifactWriteError("report output parent is unsafe")
            else:
                current.mkdir()

    def _existing(
        self, target: Path, data: bytes, artifact: ReportArtifact
    ) -> ReportArtifactWriteResult:
        metadata = target.lstat()
        if self._linked(target) or not stat.S_ISREG(metadata.st_mode):
            raise ReportArtifactWriteError("report output collision is unsafe")
        if target.read_bytes() != data:
            raise ReportArtifactWriteError("report output conflicts with existing content")
        return ReportArtifactWriteResult(artifact=artifact, created=False)

    @staticmethod
    def _linked(path: Path) -> bool:
        metadata = path.lstat()
        attributes = getattr(metadata, "st_file_attributes", 0)
        reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse)
