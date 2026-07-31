"""Bounded, read-only environment probes used by ``revanent doctor``."""

from __future__ import annotations

import os
import platform
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from revanent.agents.codex import detect_codex
from revanent.agents.opencode import detect_opencode
from revanent.agents.providers import ProviderCompatibility
from revanent.commands import EnvironmentPolicy, ExecutablePolicy, LocalCommandRunner, PathPolicy
from revanent.ports import (
    CommandFailureCategory,
    CommandRequest,
    CommandRunner,
    CommandStatus,
    OutputLimits,
)


class CheckStatus(StrEnum):
    """Normalized availability status for a doctor check."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class ToolCheck:
    """Result of one non-mutating environment capability check."""

    name: str
    category: str
    status: CheckStatus
    detail: str
    required: bool


def _first_line(value: str) -> str:
    return value.strip().splitlines()[0] if value.strip() else "version not reported"


def _host_runner(executables: tuple[str, ...]) -> CommandRunner:
    """Build the doctor runner from selected host values, never the whole environment."""
    search_path = os.environ.get("PATH", "")
    root = Path.cwd().resolve(strict=True)
    executable_policy = ExecutablePolicy.from_search_path(
        executables,
        search_path,
        windows_extensions=(".exe", ".com", ".cmd", ".bat"),
        excluded_roots=(root,),
    )
    baseline_names = (
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
    baseline = {name: os.environ[name] for name in baseline_names if name in os.environ}
    return LocalCommandRunner(
        executable_policy=executable_policy,
        path_policy=PathPolicy((root,)),
        environment_policy=EnvironmentPolicy(baseline),
    )


def _probe(
    runner: CommandRunner | None,
    executable: str,
    arguments: tuple[str, ...],
    *,
    required: bool,
    category: str,
) -> ToolCheck:
    if runner is None:
        return ToolCheck(
            executable,
            category,
            CheckStatus.UNAVAILABLE,
            "no usable configured search path",
            required,
        )
    result = runner.run(
        CommandRequest(
            executable=executable,
            arguments=arguments,
            working_directory=Path.cwd().resolve(strict=True),
            correlation_id=f"doctor-{executable}",
            timeout_seconds=10,
            output_limits=OutputLimits(
                stdout_bytes=8 * 1_024,
                stderr_bytes=8 * 1_024,
                artifact_bytes_per_stream=8 * 1_024,
            ),
        )
    )
    if (
        result.status is CommandStatus.POLICY_REJECTED
        and result.failure is not None
        and result.failure.category is CommandFailureCategory.EXECUTABLE_UNAVAILABLE
    ):
        detail = "not found on configured PATH"
    else:
        output = result.stdout.text or result.stderr.text
        detail = _first_line(output) if output else result.status.value.lower().replace("_", " ")
    status = CheckStatus.AVAILABLE if result.succeeded else CheckStatus.UNAVAILABLE
    return ToolCheck(executable, category, status, detail, required)


def detect_environment() -> tuple[ToolCheck, ...]:
    """Detect the minimal supported runtime and optional provider CLIs."""
    executable_names = ("uv", "git", "opencode", "codex")
    try:
        runner: CommandRunner | None = _host_runner(executable_names)
    except ValueError:
        runner = None
    python_status = (
        CheckStatus.AVAILABLE if sys.version_info >= (3, 12) else CheckStatus.UNSUPPORTED
    )
    python_check = ToolCheck(
        "python",
        "runtime",
        python_status,
        f"{platform.python_implementation()} {platform.python_version()}",
        True,
    )
    platform_check = ToolCheck(
        "platform",
        "runtime",
        CheckStatus.AVAILABLE,
        f"{platform.system()} {platform.machine()}",
        True,
    )
    checks = (
        python_check,
        platform_check,
        _probe(runner, "uv", ("--version",), required=True, category="runtime"),
        _probe(runner, "git", ("--version",), required=True, category="runtime"),
    )
    if runner is None:
        return (
            *checks,
            ToolCheck(
                "opencode", "provider", CheckStatus.UNAVAILABLE, "no usable provider PATH", False
            ),
            ToolCheck(
                "codex", "provider", CheckStatus.UNAVAILABLE, "no usable provider PATH", False
            ),
        )
    root = Path.cwd().resolve(strict=True)
    opencode = detect_opencode(runner, working_directory=root)
    codex = detect_codex(runner, working_directory=root)
    return (
        *checks,
        ToolCheck(
            "opencode",
            "provider",
            _provider_status(opencode.compatibility),
            opencode.version or opencode.reason or "OpenCode capability unavailable",
            False,
        ),
        ToolCheck(
            "codex",
            "provider",
            _provider_status(codex.compatibility),
            (
                f"{codex.version}; review compatible; "
                f"repair {'compatible' if codex.repair_surface_verified else 'unsupported'}"
                if codex.compatibility is ProviderCompatibility.AVAILABLE
                else codex.reason or codex.version or "Codex capability unavailable"
            ),
            False,
        ),
    )


def _provider_status(compatibility: ProviderCompatibility) -> CheckStatus:
    if compatibility is ProviderCompatibility.AVAILABLE:
        return CheckStatus.AVAILABLE
    if compatibility is ProviderCompatibility.INCOMPATIBLE:
        return CheckStatus.UNSUPPORTED
    return CheckStatus.UNAVAILABLE
