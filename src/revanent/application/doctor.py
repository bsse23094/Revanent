"""Read-only environment diagnostics with stable typed check results."""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from revanent.application.configuration import ConfigurationService
from revanent.application.provider_detection import ProviderDetectionService, ProviderStatus
from revanent.application.runtime import (
    RepositoryInspectionError,
    controlled_host_runner,
    inspect_repository,
)
from revanent.ports import CommandRequest, CommandRunner, CommandStatus, OutputLimits


class DoctorStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARNING = "WARNING"
    UNAVAILABLE = "UNAVAILABLE"
    SKIPPED = "SKIPPED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    name: str
    status: DoctorStatus
    required: bool
    detail: str


@dataclass(frozen=True, slots=True)
class DoctorResult:
    checks: tuple[DoctorCheck, ...]
    exit_code: int


class DoctorService:
    """Run only bounded runtime/config/provider observations; never initialize state."""

    def __init__(
        self,
        *,
        configuration: ConfigurationService | None = None,
        providers: ProviderDetectionService | None = None,
    ) -> None:
        self._configuration = configuration or ConfigurationService()
        self._providers = providers or ProviderDetectionService()

    def run(self, *, repository_path: Path | None, strict: bool) -> DoctorResult:
        root = _working_root(repository_path)
        checks: list[DoctorCheck] = [
            DoctorCheck(
                "python",
                DoctorStatus.PASS if sys.version_info >= (3, 12) else DoctorStatus.FAIL,
                True,
                f"{platform.python_implementation()} {platform.python_version()}",
            ),
            DoctorCheck(
                "platform", DoctorStatus.PASS, True, f"{platform.system()} {platform.machine()}"
            ),
        ]
        checks.extend(_runtime_checks(root))
        resolved_root: Path | None = None
        if repository_path is None:
            checks.append(
                DoctorCheck(
                    "repository", DoctorStatus.SKIPPED, False, "no target repository supplied"
                )
            )
            checks.append(
                DoctorCheck(
                    "configuration", DoctorStatus.SKIPPED, False, "no target repository supplied"
                )
            )
        else:
            try:
                snapshot = inspect_repository(repository_path)
            except RepositoryInspectionError as error:
                checks.append(
                    DoctorCheck("repository", DoctorStatus.FAIL, True, error.category.lower())
                )
                checks.append(
                    DoctorCheck(
                        "configuration", DoctorStatus.BLOCKED, True, "repository discovery failed"
                    )
                )
            else:
                resolved_root = snapshot.identity.worktree_root
                checks.append(
                    DoctorCheck("repository", DoctorStatus.PASS, True, "repository discovered")
                )
                config = self._configuration.validate(resolved_root)
                checks.append(
                    DoctorCheck(
                        "configuration",
                        DoctorStatus.PASS if config.valid else DoctorStatus.FAIL,
                        True,
                        config.code,
                    )
                )
        provider_root = resolved_root or root
        for capability in self._providers.detect(provider_root):
            status = _provider_doctor_status(capability.status)
            required = strict
            checks.append(
                DoctorCheck(
                    capability.provider,
                    status,
                    required,
                    capability.reason_code if capability.version is None else capability.version,
                )
            )
        ordered = tuple(checks)
        failed = any(
            check.required
            and check.status in {DoctorStatus.FAIL, DoctorStatus.BLOCKED, DoctorStatus.UNAVAILABLE}
            for check in ordered
        )
        return DoctorResult(ordered, 4 if failed else 0)


def _working_root(repository_path: Path | None) -> Path:
    target = repository_path if repository_path is not None else Path.cwd()
    try:
        return target.resolve(strict=True)
    except (OSError, RuntimeError):
        return Path.cwd().resolve(strict=True)


def _runtime_checks(root: Path) -> tuple[DoctorCheck, ...]:
    try:
        runner = controlled_host_runner(root, ("uv", "git"))
    except RepositoryInspectionError as error:
        return (
            DoctorCheck("uv", DoctorStatus.FAIL, True, error.category.lower()),
            DoctorCheck("git", DoctorStatus.FAIL, True, error.category.lower()),
        )
    return tuple(_probe_runtime(runner, root, executable) for executable in ("uv", "git"))


def _probe_runtime(runner: CommandRunner, root: Path, executable: str) -> DoctorCheck:
    result = runner.run(
        CommandRequest(
            executable=executable,
            arguments=("--version",),
            working_directory=root,
            correlation_id=f"doctor-{executable}",
            timeout_seconds=10,
            output_limits=OutputLimits(
                stdout_bytes=8 * 1_024,
                stderr_bytes=8 * 1_024,
                artifact_bytes_per_stream=8 * 1_024,
            ),
        )
    )
    if result.status is not CommandStatus.SUCCESS:
        return DoctorCheck(executable, DoctorStatus.FAIL, True, "version probe unavailable")
    output = result.stdout.text or result.stderr.text
    return DoctorCheck(executable, DoctorStatus.PASS, True, _safe_first_line(output))


def _provider_doctor_status(status: ProviderStatus) -> DoctorStatus:
    if status is ProviderStatus.AVAILABLE:
        return DoctorStatus.PASS
    if status is ProviderStatus.UNAVAILABLE:
        return DoctorStatus.UNAVAILABLE
    if status is ProviderStatus.INCOMPATIBLE:
        return DoctorStatus.WARNING
    return DoctorStatus.BLOCKED


def _safe_first_line(value: str) -> str:
    line = value.strip().splitlines()[0] if value.strip() else "version not reported"
    cleaned = " ".join("".join(item if item.isprintable() else " " for item in line).split())
    return cleaned[:160] or "version not reported"
