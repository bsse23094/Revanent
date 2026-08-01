"""Typed read-only configuration validation use case."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from revanent.config import (
    ConfigurationError,
    EffectiveConfiguration,
    load_effective_config,
    resolve_project_paths,
)


@dataclass(frozen=True, slots=True)
class ConfigurationValidationResult:
    valid: bool
    code: str
    message: str
    effective: EffectiveConfiguration | None = None


class ConfigurationService:
    """Locate and validate a complete effective schema-v1 project configuration."""

    def validate(
        self,
        repository_root: Path,
        *,
        config_path: Path | None = None,
        max_total_minutes: int | None = None,
    ) -> ConfigurationValidationResult:
        try:
            effective = load_effective_config(
                repository_root,
                explicit_path=config_path,
                max_total_minutes=max_total_minutes,
            )
            resolve_project_paths(effective)
        except ConfigurationError as error:
            return ConfigurationValidationResult(
                False, "invalid_configuration", _safe_message(error)
            )
        return ConfigurationValidationResult(
            True,
            "valid",
            "configuration is valid",
            effective,
        )


def _safe_message(error: Exception) -> str:
    """Keep public validation output bounded and independent of input values."""
    return " ".join(str(error).replace("\x00", " ").split())[:512] or "configuration is invalid"
