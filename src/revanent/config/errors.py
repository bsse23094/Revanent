"""Configuration boundary errors with safe, explicit categories."""


class ConfigurationError(Exception):
    """Base class for project configuration failures."""


class ConfigurationLoadError(ConfigurationError):
    """Configuration could not be read or parsed as a YAML mapping."""


class UnsupportedConfigurationVersionError(ConfigurationError):
    """The configuration schema version is missing or unsupported."""


class ConfigurationValidationError(ConfigurationError):
    """Parsed configuration did not satisfy the versioned schema."""
