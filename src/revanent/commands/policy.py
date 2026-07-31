"""Central executable, environment, and filesystem policies for commands."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType

from revanent.ports.commands import (
    MAX_ENVIRONMENT_ENTRIES,
    MAX_ENVIRONMENT_VALUE_BYTES,
    MAX_STDIN_BYTES,
    MAX_STREAM_ARTIFACT_BYTES,
    MAX_STREAM_CAPTURE_BYTES,
    MAX_TIMEOUT_SECONDS,
    CommandPolicyError,
    CommandRequest,
    EnvironmentPolicyError,
    ExecutablePolicyError,
    ExecutableUnavailableError,
    OutputArtifactPolicyError,
    WorkingDirectoryPolicyError,
)

_EXECUTABLE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}$")
_ENVIRONMENT_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SENSITIVE_KEY = re.compile(
    r"(?:^|_)(?:api_?key|auth|authorization|credential|password|passwd|secret|token)(?:$|_)",
    re.IGNORECASE,
)


def _is_unc(path: Path) -> bool:
    rendered = str(path).replace("/", "\\")
    return rendered.startswith("\\\\")


def _is_absolute_on_any_supported_platform(path: Path) -> bool:
    rendered = str(path)
    return (
        path.is_absolute()
        or PureWindowsPath(rendered).is_absolute()
        or PurePosixPath(rendered).is_absolute()
    )


def _contains(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


@dataclass(frozen=True, slots=True)
class CommandPolicy:
    """Runner-specific resource ceilings below the absolute contract maxima."""

    max_timeout_seconds: float = MAX_TIMEOUT_SECONDS
    max_stdout_bytes: int = MAX_STREAM_CAPTURE_BYTES
    max_stderr_bytes: int = MAX_STREAM_CAPTURE_BYTES
    max_artifact_bytes_per_stream: int = MAX_STREAM_ARTIFACT_BYTES
    max_stdin_bytes: int = MAX_STDIN_BYTES
    allow_stdin: bool = False
    allow_artifacts: bool = True

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_timeout_seconds, bool)
            or not isinstance(self.max_timeout_seconds, int | float)
            or not 0 < self.max_timeout_seconds <= MAX_TIMEOUT_SECONDS
        ):
            raise ValueError("command policy timeout ceiling is invalid")
        for name, value, maximum in (
            ("stdout", self.max_stdout_bytes, MAX_STREAM_CAPTURE_BYTES),
            ("stderr", self.max_stderr_bytes, MAX_STREAM_CAPTURE_BYTES),
            ("artifact", self.max_artifact_bytes_per_stream, MAX_STREAM_ARTIFACT_BYTES),
        ):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"command policy {name} ceiling is invalid")
        if (
            type(self.max_stdin_bytes) is not int
            or not 0 <= self.max_stdin_bytes <= MAX_STDIN_BYTES
        ):
            raise ValueError("command policy stdin ceiling is invalid")

    def validate(self, request: CommandRequest) -> None:
        if request.timeout_seconds > self.max_timeout_seconds:
            raise CommandPolicyError("command timeout exceeds runner policy")
        if request.output_limits.stdout_bytes > self.max_stdout_bytes:
            raise CommandPolicyError("stdout capture exceeds runner policy")
        if request.output_limits.stderr_bytes > self.max_stderr_bytes:
            raise CommandPolicyError("stderr capture exceeds runner policy")
        if request.output_limits.artifact_bytes_per_stream > self.max_artifact_bytes_per_stream:
            raise CommandPolicyError("artifact capture exceeds runner policy")
        if request.stdin is not None and (
            not self.allow_stdin or len(request.stdin) > self.max_stdin_bytes
        ):
            raise CommandPolicyError("standard input is not authorized by runner policy")
        if request.artifact_directory is not None and not self.allow_artifacts:
            raise CommandPolicyError("output artifacts are not authorized by runner policy")


@dataclass(frozen=True, slots=True)
class ExecutableRule:
    """One allowed name and its deterministic candidate order."""

    name: str
    candidates: tuple[Path, ...]
    allowed_extensions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _EXECUTABLE_NAME.fullmatch(self.name) or any(
            separator in self.name for separator in ("/", "\\")
        ):
            raise ValueError("executable rule names must be simple names")
        if not self.candidates:
            raise ValueError("an executable rule requires at least one candidate")
        normalized_extensions = tuple(
            extension.casefold() if extension.startswith(".") else f".{extension.casefold()}"
            for extension in self.allowed_extensions
        )
        if len(normalized_extensions) != len(set(normalized_extensions)) or any(
            not re.fullmatch(r"\.[a-z0-9]{1,15}", extension) for extension in normalized_extensions
        ):
            raise ValueError("authorized executable extensions must be unique simple suffixes")
        for candidate in self.candidates:
            if not isinstance(candidate, Path) or not candidate.is_absolute():
                raise ValueError("executable candidates must be absolute pathlib paths")
            if "\x00" in str(candidate):
                raise ValueError("executable candidates cannot contain null bytes")
            if normalized_extensions and candidate.suffix.casefold() not in normalized_extensions:
                raise ValueError("executable candidate extension is not explicitly authorized")
        object.__setattr__(self, "candidates", tuple(self.candidates))
        object.__setattr__(self, "allowed_extensions", normalized_extensions)


@dataclass(frozen=True, slots=True)
class ExecutablePolicy:
    """Fail-closed name allowlist with explicit, ordered candidate identities."""

    rules: tuple[ExecutableRule, ...]
    windows: bool = field(default_factory=lambda: os.name == "nt")
    _rules_by_name: Mapping[str, ExecutableRule] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        rules_by_name: dict[str, ExecutableRule] = {}
        for rule in self.rules:
            key = rule.name.casefold() if self.windows else rule.name
            if key in rules_by_name:
                raise ValueError("executable rule names must be unique")
            rules_by_name[key] = rule
        if not rules_by_name:
            raise ValueError("executable policy requires at least one rule")
        object.__setattr__(self, "rules", tuple(self.rules))
        object.__setattr__(self, "_rules_by_name", MappingProxyType(rules_by_name))

    @classmethod
    def from_search_path(
        cls,
        names: tuple[str, ...],
        search_path: str,
        *,
        windows: bool | None = None,
        windows_extensions: tuple[str, ...] = (".exe", ".com"),
        excluded_roots: tuple[Path, ...] = (),
    ) -> ExecutablePolicy:
        """Freeze candidates from an explicit PATH value without consulting cwd."""
        use_windows = os.name == "nt" if windows is None else windows
        separator = ";" if use_windows else ":"
        if any(not isinstance(root, Path) or not root.is_absolute() for root in excluded_roots):
            raise ValueError("excluded executable roots must be absolute pathlib paths")
        excluded = tuple(root.resolve(strict=False) for root in excluded_roots)
        directories: list[Path] = []
        for raw_directory in search_path.split(separator):
            if not raw_directory:
                continue
            directory = Path(raw_directory)
            if not directory.is_absolute() or _is_unc(directory):
                continue
            resolved_directory = directory.resolve(strict=False)
            if any(_contains(root, resolved_directory) for root in excluded):
                continue
            if directory not in directories:
                directories.append(directory)
        if not directories:
            raise ValueError("search path has no absolute local directories")

        extensions = tuple(
            extension.casefold() if extension.startswith(".") else f".{extension.casefold()}"
            for extension in windows_extensions
        )
        rules = []
        for name in names:
            if not _EXECUTABLE_NAME.fullmatch(name) or any(
                separator_ in name for separator_ in ("/", "\\")
            ):
                raise ValueError("executable names must be simple names")
            suffix = Path(name).suffix.casefold()
            candidate_names: tuple[str, ...]
            allowed_extensions: tuple[str, ...]
            if use_windows:
                if suffix:
                    if suffix not in extensions:
                        raise ValueError("requested executable extension is not authorized")
                    candidate_names = (name,)
                    allowed_extensions = (suffix,)
                else:
                    candidate_names = tuple(f"{name}{extension}" for extension in extensions)
                    allowed_extensions = extensions
            else:
                candidate_names = (name,)
                allowed_extensions = ()
            candidates = tuple(
                directory / candidate_name
                for directory in directories
                for candidate_name in candidate_names
            )
            rules.append(
                ExecutableRule(
                    name=name,
                    candidates=candidates,
                    allowed_extensions=allowed_extensions,
                )
            )
        return cls(tuple(rules), windows=use_windows)

    def resolve(self, executable: str) -> Path:
        """Resolve only a configured simple name, choosing the first usable candidate."""
        if not _EXECUTABLE_NAME.fullmatch(executable) or any(
            separator in executable for separator in ("/", "\\")
        ):
            raise ExecutablePolicyError("executable must be an authorized simple name")
        if PureWindowsPath(executable).drive:
            raise ExecutablePolicyError("executable paths are not accepted")
        key = executable.casefold() if self.windows else executable
        rule = self._rules_by_name.get(key)
        if rule is None:
            raise ExecutablePolicyError("executable is not authorized by policy")

        for candidate in rule.candidates:
            try:
                resolved = candidate.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if not resolved.is_file():
                continue
            if rule.allowed_extensions and (
                candidate.suffix.casefold() not in rule.allowed_extensions
                or resolved.suffix.casefold() not in rule.allowed_extensions
            ):
                continue
            if not self.windows and not os.access(resolved, os.X_OK):
                continue
            return resolved
        raise ExecutableUnavailableError("authorized executable is unavailable")


@dataclass(frozen=True, slots=True)
class PathPolicy:
    """Resolved containment policy for working directories and command artifacts."""

    approved_roots: tuple[Path, ...]
    artifact_roots: tuple[Path, ...] = ()
    allow_unc: bool = False
    allow_filesystem_root: bool = False

    def __post_init__(self) -> None:
        approved = self._resolve_roots(self.approved_roots, "approved")
        artifacts = self._resolve_roots(self.artifact_roots, "artifact")
        if not approved:
            raise ValueError("path policy requires at least one approved root")
        object.__setattr__(self, "approved_roots", approved)
        object.__setattr__(self, "artifact_roots", artifacts)

    def _resolve_roots(self, roots: tuple[Path, ...], label: str) -> tuple[Path, ...]:
        resolved_roots: list[Path] = []
        for root in roots:
            if not isinstance(root, Path):
                raise ValueError(f"{label} roots must be absolute pathlib paths")
            if _is_unc(root) and not self.allow_unc:
                raise ValueError(f"{label} UNC roots require explicit authorization")
            if not root.is_absolute():
                raise ValueError(f"{label} roots must be absolute pathlib paths")
            try:
                resolved = root.resolve(strict=True)
            except (OSError, RuntimeError) as error:
                raise ValueError(f"{label} root must be an existing directory") from error
            if not resolved.is_dir():
                raise ValueError(f"{label} root must be an existing directory")
            if resolved == Path(resolved.anchor) and not self.allow_filesystem_root:
                raise ValueError("filesystem roots require explicit authorization")
            if resolved not in resolved_roots:
                resolved_roots.append(resolved)
        return tuple(resolved_roots)

    def resolve_working_directory(self, path: Path) -> Path:
        """Require an existing resolved directory inside one approved root."""
        if not isinstance(path, Path) or not path.is_absolute():
            raise WorkingDirectoryPolicyError("working directory must be an absolute pathlib path")
        if _is_unc(path) and not self.allow_unc:
            raise WorkingDirectoryPolicyError("UNC working directories are not authorized")
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise WorkingDirectoryPolicyError("working directory is unavailable") from error
        if not resolved.is_dir():
            raise WorkingDirectoryPolicyError("working directory is not a directory")
        if not any(_contains(root, resolved) for root in self.approved_roots):
            raise WorkingDirectoryPolicyError("working directory is outside approved roots")
        return resolved

    def resolve_relative(
        self,
        root: Path,
        relative_path: Path,
        *,
        must_exist: bool,
    ) -> Path:
        """Resolve a repository-relative path without absolute or traversal bypasses."""
        canonical_root = self.resolve_working_directory(root)
        if not isinstance(relative_path, Path) or _is_absolute_on_any_supported_platform(
            relative_path
        ):
            raise WorkingDirectoryPolicyError("path must be relative to its approved root")
        if ".." in PurePosixPath(str(relative_path).replace("\\", "/")).parts:
            raise WorkingDirectoryPolicyError("relative path cannot contain parent traversal")
        try:
            resolved = (canonical_root / relative_path).resolve(strict=must_exist)
        except (OSError, RuntimeError) as error:
            raise WorkingDirectoryPolicyError("relative path is unavailable") from error
        if not _contains(canonical_root, resolved):
            raise WorkingDirectoryPolicyError("relative path escapes its approved root")
        return resolved

    def resolve_artifact_directory(self, path: Path) -> Path:
        """Require an existing directory under a separately configured artifact root."""
        if not self.artifact_roots:
            raise OutputArtifactPolicyError("command artifacts are not enabled")
        if not isinstance(path, Path) or not path.is_absolute():
            raise OutputArtifactPolicyError("artifact directory must be an absolute pathlib path")
        if _is_unc(path) and not self.allow_unc:
            raise OutputArtifactPolicyError("UNC artifact directories are not authorized")
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise OutputArtifactPolicyError("artifact directory is unavailable") from error
        if not resolved.is_dir():
            raise OutputArtifactPolicyError("artifact directory is not a directory")
        if not any(_contains(root, resolved) for root in self.artifact_roots):
            raise OutputArtifactPolicyError("artifact directory is outside artifact roots")
        return resolved

    def artifact_path(self, directory: Path, filename: str) -> Path:
        """Build one contained artifact path from a safe, runner-owned filename."""
        canonical_directory = self.resolve_artifact_directory(directory)
        if (
            not filename
            or Path(filename).name != filename
            or any(separator in filename for separator in ("/", "\\"))
            or "\x00" in filename
        ):
            raise OutputArtifactPolicyError("artifact filename is invalid")
        target = (canonical_directory / filename).resolve(strict=False)
        if not _contains(canonical_directory, target):
            raise OutputArtifactPolicyError("artifact target escapes its directory")
        return target


@dataclass(frozen=True, slots=True)
class EnvironmentPolicy:
    """Construct a child environment from a bounded baseline and explicit overrides."""

    baseline: Mapping[str, str] = field(repr=False)
    allowed_override_keys: frozenset[str] = frozenset()
    forbidden_keys: frozenset[str] = frozenset()
    allowed_sensitive_keys: frozenset[str] = frozenset()
    windows: bool = field(default_factory=lambda: os.name == "nt")
    _baseline: Mapping[str, str] = field(init=False, repr=False)
    _allowed: frozenset[str] = field(init=False, repr=False)
    _forbidden: frozenset[str] = field(init=False, repr=False)
    _sensitive: frozenset[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if len(self.baseline) > MAX_ENVIRONMENT_ENTRIES:
            raise ValueError(
                f"baseline environment is limited to {MAX_ENVIRONMENT_ENTRIES} entries"
            )
        if len(self.allowed_override_keys | self.forbidden_keys | self.allowed_sensitive_keys) > (
            MAX_ENVIRONMENT_ENTRIES * 3
        ):
            raise ValueError("environment policy key sets are unreasonably large")
        allowed = frozenset(self._key(key) for key in self.allowed_override_keys)
        forbidden = frozenset(self._key(key) for key in self.forbidden_keys)
        sensitive = frozenset(self._key(key) for key in self.allowed_sensitive_keys)
        if forbidden & sensitive:
            raise ValueError("environment keys cannot be both forbidden and sensitive-allowed")
        baseline: dict[str, str] = {}
        for key, value in self.baseline.items():
            normalized = self._validate_pair(key, value)
            if normalized in forbidden:
                raise ValueError("baseline contains a forbidden environment key")
            if _SENSITIVE_KEY.search(normalized) and normalized not in sensitive:
                raise ValueError("baseline contains an unauthorized sensitive environment key")
            if normalized in baseline:
                raise ValueError("baseline environment keys collide after normalization")
            baseline[normalized] = value
        object.__setattr__(self, "baseline", MappingProxyType(dict(self.baseline)))
        object.__setattr__(self, "allowed_override_keys", frozenset(self.allowed_override_keys))
        object.__setattr__(self, "forbidden_keys", frozenset(self.forbidden_keys))
        object.__setattr__(self, "allowed_sensitive_keys", frozenset(self.allowed_sensitive_keys))
        object.__setattr__(self, "_baseline", MappingProxyType(baseline))
        object.__setattr__(self, "_allowed", allowed)
        object.__setattr__(self, "_forbidden", forbidden)
        object.__setattr__(self, "_sensitive", sensitive)

    def _key(self, key: str) -> str:
        return key.upper() if self.windows else key

    def _validate_pair(self, key: str, value: str) -> str:
        if (
            not isinstance(key, str)
            or not isinstance(value, str)
            or not _ENVIRONMENT_KEY.fullmatch(key)
            or "\x00" in key
            or "\x00" in value
        ):
            raise ValueError("environment keys and values use invalid process syntax")
        if len(value.encode("utf-8", errors="surrogatepass")) > MAX_ENVIRONMENT_VALUE_BYTES:
            raise ValueError(
                f"environment values are limited to {MAX_ENVIRONMENT_VALUE_BYTES} bytes"
            )
        return self._key(key)

    def build(self, overrides: Mapping[str, str]) -> dict[str, str]:
        """Apply permitted overrides to the baseline with deterministic precedence."""
        result = dict(self._baseline)
        seen: set[str] = set()
        for key, value in overrides.items():
            try:
                normalized = self._validate_pair(key, value)
            except ValueError as error:
                raise EnvironmentPolicyError("environment override is malformed") from error
            if normalized in seen:
                raise EnvironmentPolicyError("environment keys collide after normalization")
            seen.add(normalized)
            if normalized in self._forbidden:
                raise EnvironmentPolicyError("environment key is forbidden")
            if normalized not in self._allowed:
                raise EnvironmentPolicyError("environment key is not authorized")
            if _SENSITIVE_KEY.search(normalized) and normalized not in self._sensitive:
                raise EnvironmentPolicyError("sensitive environment key is not authorized")
            result[normalized] = value
        if len(result) > MAX_ENVIRONMENT_ENTRIES:
            raise EnvironmentPolicyError("constructed child environment has too many entries")
        return result

    def sensitive_values(self, environment: Mapping[str, str]) -> tuple[str, ...]:
        """Return values that must be added to result redaction for this child."""
        values = []
        for key, value in environment.items():
            normalized = self._key(key)
            if (normalized in self._sensitive or _SENSITIVE_KEY.search(normalized)) and value:
                values.append(value)
        return tuple(values)
