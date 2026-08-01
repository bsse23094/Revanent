from __future__ import annotations

from pathlib import Path

import pytest

from revanent.application.configuration import ConfigurationService
from revanent.config import (
    CONFIGURATION_FILENAME,
    ConfigurationSource,
    ProjectConfigurationError,
    configuration_path,
    load_config,
    load_effective_config,
    render_default_config,
)


def _write_default(root: Path) -> Path:
    path = root / CONFIGURATION_FILENAME
    path.write_bytes(render_default_config(root.name))
    return path


def test_generated_default_validates_through_production_loader(tmp_path: Path) -> None:
    path = _write_default(tmp_path)

    config = load_config(path)

    assert config.project.name == tmp_path.name
    assert config.policy.allow_network is False
    assert config.policy.allow_live_opencode_builder is False
    assert config.policy.allow_live_codex_reviewer is False
    assert config.policy.allow_push is False
    assert config.workspace.root != config.reporting.directory


def test_effective_configuration_uses_only_reviewed_cli_override(tmp_path: Path) -> None:
    _write_default(tmp_path)

    effective = load_effective_config(
        tmp_path, max_total_minutes=45, environment={"UNUSED": "secret"}
    )

    assert effective.config.budgets.max_total_minutes == 45
    assert effective.max_total_minutes_source is ConfigurationSource.CLI
    assert effective.path == tmp_path / CONFIGURATION_FILENAME


def test_configuration_path_is_root_bound_and_rejects_sibling_or_nested_paths(
    tmp_path: Path,
) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()

    assert (
        configuration_path(tmp_path, Path(CONFIGURATION_FILENAME))
        == tmp_path / CONFIGURATION_FILENAME
    )
    with pytest.raises(ProjectConfigurationError):
        configuration_path(tmp_path, nested / CONFIGURATION_FILENAME)
    with pytest.raises(ProjectConfigurationError):
        configuration_path(tmp_path, tmp_path.parent / CONFIGURATION_FILENAME)


def test_validation_failure_does_not_echo_secret_value(tmp_path: Path) -> None:
    path = _write_default(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8") + "\nunknown_secret: top-secret-value\n", encoding="utf-8"
    )

    result = ConfigurationService().validate(tmp_path)

    assert result.valid is False
    assert "top-secret-value" not in result.message


def test_oversized_configuration_is_rejected_without_parsing(tmp_path: Path) -> None:
    path = tmp_path / CONFIGURATION_FILENAME
    path.write_bytes(b"#" * (256 * 1_024 + 1))

    result = ConfigurationService().validate(tmp_path)

    assert result.valid is False
    assert result.code == "invalid_configuration"
