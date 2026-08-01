"""Versioned project configuration boundary."""

from revanent.config.errors import (
    ConfigurationError,
    ConfigurationLoadError,
    ConfigurationValidationError,
    UnsupportedConfigurationVersionError,
)
from revanent.config.loader import load_config
from revanent.config.models import RevanentConfig
from revanent.config.project import (
    CONFIGURATION_FILENAME,
    ConfigurationSource,
    EffectiveConfiguration,
    ProjectConfigurationError,
    ProjectPaths,
    configuration_path,
    default_config,
    load_effective_config,
    normalize_repository_root,
    render_default_config,
    resolve_project_paths,
)

__all__ = [
    "CONFIGURATION_FILENAME",
    "ConfigurationError",
    "ConfigurationLoadError",
    "ConfigurationSource",
    "ConfigurationValidationError",
    "EffectiveConfiguration",
    "ProjectConfigurationError",
    "ProjectPaths",
    "RevanentConfig",
    "UnsupportedConfigurationVersionError",
    "configuration_path",
    "default_config",
    "load_config",
    "load_effective_config",
    "normalize_repository_root",
    "render_default_config",
    "resolve_project_paths",
]
