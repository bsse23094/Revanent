from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from revanent.config import (
    ConfigurationLoadError,
    ConfigurationValidationError,
    RevanentConfig,
    UnsupportedConfigurationVersionError,
    load_config,
)


def _config_document() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "project": {"name": "sample"},
        "workspace": {
            "strategy": "git-worktree",
            "root": ".revanent/worktrees",
            "preserve_failed": True,
        },
        "builder": {
            "provider": "opencode",
            "model": "local-model",
            "max_attempts": 3,
            "timeout_seconds": 1_800,
        },
        "reviewer": {
            "provider": "codex",
            "mode": "review_then_repair",
            "max_reviews": 3,
            "max_repairs": 2,
            "timeout_seconds": 1_800,
        },
        "validation": {
            "commands": [
                {"name": "tests", "command": ["uv", "run", "pytest"]},
            ]
        },
        "budgets": {
            "max_total_minutes": 90,
            "max_remote_tokens": None,
            "max_estimated_cost_usd": None,
        },
        "policy": {
            "allowed_paths": ["src/**", "tests/**"],
            "forbidden_paths": [".git/**", ".env"],
            "allow_codex_write_repair": True,
            "allow_network": False,
            "allow_push": False,
            "allow_merge": False,
        },
        "reporting": {
            "directory": ".revanent/runs",
            "formats": ["json", "markdown"],
        },
    }


def _write_yaml(path: Path, document: object) -> None:
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def test_example_configuration_loads_and_round_trips_deterministically() -> None:
    config = load_config(Path("revanent.example.yaml"))

    restored = RevanentConfig.model_validate_json(config.model_dump_json())

    assert config.project.name == "example-project"
    assert restored == config
    assert restored.model_dump_json() == config.model_dump_json()


@pytest.mark.parametrize("version", [None, 0, 2, "1", True])
def test_loader_rejects_missing_or_unknown_schema_version(tmp_path: Path, version: object) -> None:
    document = _config_document()
    if version is None:
        document.pop("schema_version")
    else:
        document["schema_version"] = version
    path = tmp_path / "revanent.yaml"
    _write_yaml(path, document)

    with pytest.raises(UnsupportedConfigurationVersionError):
        load_config(path)


def test_loader_rejects_missing_invalid_and_nonmapping_yaml(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationLoadError, match="cannot read"):
        load_config(tmp_path / "missing.yaml")

    invalid = tmp_path / "invalid.yaml"
    invalid.write_text("project: [", encoding="utf-8")
    with pytest.raises(ConfigurationLoadError, match="not valid YAML"):
        load_config(invalid)

    sequence = tmp_path / "sequence.yaml"
    _write_yaml(sequence, ["not", "a", "mapping"])
    with pytest.raises(ConfigurationLoadError, match="root must be a mapping"):
        load_config(sequence)


def test_loader_wraps_schema_validation_without_exposing_yaml_values(tmp_path: Path) -> None:
    document = _config_document()
    document["unexpected"] = "rejected"
    path = tmp_path / "revanent.yaml"
    _write_yaml(path, document)

    with pytest.raises(ConfigurationValidationError, match="Extra inputs are not permitted"):
        load_config(path)


@pytest.mark.parametrize(
    "unsafe_path",
    ["../outside", "C:/outside", "/absolute", "**"],
)
def test_configuration_rejects_unsafe_allowed_paths(unsafe_path: str) -> None:
    document = _config_document()
    document["policy"]["allowed_paths"] = [unsafe_path]

    with pytest.raises(ValidationError):
        RevanentConfig.model_validate(document)


@pytest.mark.parametrize("key", ["allow_push", "allow_merge"])
def test_configuration_cannot_enable_unapproved_git_publication(key: str) -> None:
    document = _config_document()
    document["policy"][key] = True

    with pytest.raises(ValidationError):
        RevanentConfig.model_validate(document)


def test_configuration_rejects_unsafe_cross_field_combinations() -> None:
    document = _config_document()
    document["reviewer"]["mode"] = "review_only"
    with pytest.raises(ValidationError, match="max_repairs=0"):
        RevanentConfig.model_validate(document)

    document = _config_document()
    document["reporting"]["directory"] = ".revanent/worktrees"
    with pytest.raises(ValidationError, match="must be distinct"):
        RevanentConfig.model_validate(document)

    document = _config_document()
    document["policy"]["forbidden_paths"] = ["src/**"]
    with pytest.raises(ValidationError, match="both allowed and forbidden"):
        RevanentConfig.model_validate(document)


def test_configuration_normalizes_windows_separators_and_is_immutable() -> None:
    document = _config_document()
    document["workspace"]["root"] = ".revanent\\worktrees"
    document["policy"]["allowed_paths"] = ["src\\**"]
    config = RevanentConfig.model_validate(document)

    assert config.workspace.root == ".revanent/worktrees"
    assert config.policy.allowed_paths == ("src/**",)
    with pytest.raises(ValidationError):
        config.policy.__setattr__("allow_network", True)


def test_configuration_rejects_duplicate_commands_and_unbounded_values() -> None:
    document = _config_document()
    document["validation"]["commands"].append({"name": "tests", "command": ["uv", "run", "pytest"]})
    with pytest.raises(ValidationError, match="names must be unique"):
        RevanentConfig.model_validate(document)

    document = _config_document()
    document["budgets"]["max_total_minutes"] = 0
    with pytest.raises(ValidationError):
        RevanentConfig.model_validate(document)
