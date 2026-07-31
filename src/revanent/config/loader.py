"""Safe YAML loading into the strict configuration boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from revanent.config.errors import (
    ConfigurationLoadError,
    ConfigurationValidationError,
    UnsupportedConfigurationVersionError,
)
from revanent.config.models import RevanentConfig

CONFIG_SCHEMA_VERSION = 1


def load_config(path: Path) -> RevanentConfig:
    """Load one YAML file without resolving tags or consulting the environment."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        detail = f"cannot read configuration: {type(error).__name__}"
        raise ConfigurationLoadError(detail) from error
    try:
        document: Any = yaml.safe_load(raw)
    except yaml.YAMLError as error:
        raise ConfigurationLoadError("configuration is not valid YAML") from error
    if not isinstance(document, dict):
        raise ConfigurationLoadError("configuration root must be a mapping")

    version = document.get("schema_version")
    if type(version) is not int or version != CONFIG_SCHEMA_VERSION:
        detail = (
            f"unsupported configuration schema version {version!r}; "
            f"expected {CONFIG_SCHEMA_VERSION}"
        )
        raise UnsupportedConfigurationVersionError(detail)
    try:
        return RevanentConfig.model_validate(document)
    except ValidationError as error:
        issues = error.errors(include_url=False, include_context=False, include_input=False)
        details = []
        for issue in issues:
            location = ".".join(str(part) for part in issue["loc"])
            details.append(f"{location or '<root>'}: {issue['msg']}")
        raise ConfigurationValidationError("; ".join(details)) from error
