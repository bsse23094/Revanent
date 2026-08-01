"""Safe YAML loading into the strict configuration boundary."""

from __future__ import annotations

import os
import stat
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
MAX_CONFIGURATION_BYTES = 256 * 1_024


def _read_configuration(path: Path) -> str:
    """Read one bounded regular configuration file without following a link."""
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise OSError("configuration must be a regular file")
        if metadata.st_size > MAX_CONFIGURATION_BYTES:
            raise OSError("configuration exceeds the size limit")
        flags = os.O_RDONLY
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_size > MAX_CONFIGURATION_BYTES:
                raise OSError("configuration must be a bounded regular file")
            chunks: list[bytes] = []
            remaining = MAX_CONFIGURATION_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(16 * 1_024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
        finally:
            os.close(descriptor)
        if len(data) > MAX_CONFIGURATION_BYTES:
            raise OSError("configuration exceeds the size limit")
        return data.decode("utf-8")
    except (OSError, UnicodeError) as error:
        detail = f"cannot read configuration: {type(error).__name__}"
        raise ConfigurationLoadError(detail) from error


def load_config(path: Path) -> RevanentConfig:
    """Load one YAML file without resolving tags or consulting the environment."""
    raw = _read_configuration(path)
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
