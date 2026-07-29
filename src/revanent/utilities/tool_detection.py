"""Bounded, read-only environment probes used by ``revanent doctor``."""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import StrEnum


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


def _probe(
    executable: str, arguments: tuple[str, ...], *, required: bool, category: str
) -> ToolCheck:
    path = shutil.which(executable)
    if path is None:
        return ToolCheck(
            executable, category, CheckStatus.UNAVAILABLE, "not found on PATH", required
        )
    try:
        result = subprocess.run(
            [path, *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return ToolCheck(
            executable, category, CheckStatus.UNAVAILABLE, type(error).__name__, required
        )

    output = result.stdout or result.stderr
    status = CheckStatus.AVAILABLE if result.returncode == 0 else CheckStatus.UNAVAILABLE
    return ToolCheck(executable, category, status, _first_line(output), required)


def detect_environment() -> tuple[ToolCheck, ...]:
    """Detect the minimal supported runtime and optional provider CLIs."""
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
    return (
        python_check,
        platform_check,
        _probe("uv", ("--version",), required=True, category="runtime"),
        _probe("git", ("--version",), required=True, category="runtime"),
        _probe("opencode", ("--version",), required=False, category="provider"),
        _probe("codex", ("--version",), required=False, category="provider"),
    )
