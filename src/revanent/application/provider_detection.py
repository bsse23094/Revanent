"""Read-only P3-backed provider capability detection for P6 presentation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from revanent.agents.codex import detect_codex
from revanent.agents.opencode import detect_opencode
from revanent.agents.providers import ProviderCompatibility
from revanent.application.runtime import RepositoryInspectionError, controlled_host_runner


class ProviderStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    INCOMPATIBLE = "INCOMPATIBLE"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class ProviderCapability:
    provider: str
    status: ProviderStatus
    version: str | None
    review: bool
    repair: bool
    builder: bool
    reason_code: str


class ProviderDetectionService:
    """Adapt only existing version/help capability facts into bounded result models."""

    def detect(self, repository_root: Path) -> tuple[ProviderCapability, ...]:
        try:
            runner = controlled_host_runner(repository_root, ("opencode", "codex"))
        except RepositoryInspectionError as error:
            return (
                ProviderCapability(
                    "opencode", ProviderStatus.BLOCKED, None, False, False, False, error.category
                ),
                ProviderCapability(
                    "codex", ProviderStatus.BLOCKED, None, False, False, False, error.category
                ),
            )
        opencode = detect_opencode(runner, working_directory=repository_root)
        codex = detect_codex(runner, working_directory=repository_root)
        return (
            ProviderCapability(
                provider="opencode",
                status=_status(opencode.compatibility),
                version=_safe_text(opencode.version),
                review=False,
                repair=False,
                builder=opencode.compatibility is ProviderCompatibility.AVAILABLE,
                reason_code=_reason_code(opencode.reason),
            ),
            ProviderCapability(
                provider="codex",
                status=_status(codex.compatibility),
                version=_safe_text(codex.version),
                review=codex.compatibility is ProviderCompatibility.AVAILABLE,
                repair=codex.repair_surface_verified,
                builder=False,
                reason_code=_reason_code(codex.reason),
            ),
        )


def _status(value: ProviderCompatibility) -> ProviderStatus:
    return ProviderStatus(value.value)


def _safe_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(
        "".join(character if character.isprintable() else " " for character in value).split()
    )
    return cleaned[:128] or None


def _reason_code(value: str | None) -> str:
    if value is None:
        return "compatible"
    folded = value.casefold()
    if "not found" in folded:
        return "executable_not_found"
    if "does not prove" in folded:
        return "surface_unverified"
    if "path" in folded:
        return "path_unavailable"
    return "probe_unavailable"
