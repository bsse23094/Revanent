from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from revanent.cli.app import app
from revanent.commands import (
    CommandPolicy,
    EnvironmentPolicy,
    ExecutablePolicy,
    ExecutableRule,
    LocalCommandRunner,
    PathPolicy,
)
from revanent.ports import CommandRequest, CommandStatus, OutputLimits

runner = CliRunner()

_GIT_KEYS = frozenset(
    {
        "GIT_CONFIG_NOSYSTEM",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_KEY_0",
        "GIT_CONFIG_VALUE_0",
        "GIT_CONFIG_KEY_1",
        "GIT_CONFIG_VALUE_1",
        "GIT_ATTR_NOSYSTEM",
        "GIT_TERMINAL_PROMPT",
        "GCM_INTERACTIVE",
        "GIT_PAGER",
        "PAGER",
        "GIT_EDITOR",
        "GIT_SEQUENCE_EDITOR",
        "LC_ALL",
        "LANG",
    }
)


def _git_runner(root: Path) -> LocalCommandRunner:
    raw_git = shutil.which("git")
    if raw_git is None:
        pytest.fail("setup CLI integration requires Git")
    executable = Path(raw_git).resolve(strict=True)
    extensions = (executable.suffix,) if os.name == "nt" else ()
    baseline = {
        key: os.environ[key]
        for key in ("SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP")
        if key in os.environ
    }
    return LocalCommandRunner(
        executable_policy=ExecutablePolicy((ExecutableRule("git", (executable,), extensions),)),
        path_policy=PathPolicy((root,)),
        environment_policy=EnvironmentPolicy(baseline, allowed_override_keys=_GIT_KEYS),
        command_policy=CommandPolicy(
            max_timeout_seconds=30,
            max_stdout_bytes=64 * 1_024,
            max_stderr_bytes=64 * 1_024,
            max_artifact_bytes_per_stream=1,
            allow_artifacts=False,
        ),
    )


def _git(runner_: LocalCommandRunner, repository: Path, arguments: tuple[str, ...]) -> None:
    result = runner_.run(
        CommandRequest(
            executable="git",
            arguments=arguments,
            working_directory=repository,
            correlation_id="p6-setup-test",
            timeout_seconds=30,
            output_limits=OutputLimits(
                stdout_bytes=64 * 1_024,
                stderr_bytes=64 * 1_024,
                artifact_bytes_per_stream=1,
            ),
        )
    )
    assert result.status is CommandStatus.SUCCESS, result.stderr.text


def _repository(tmp_path: Path, *, name: str = "repository with spaces") -> Path:
    root = tmp_path.resolve(strict=True)
    repository = root / name
    repository.mkdir()
    controlled = _git_runner(root)
    _git(controlled, repository, ("init", "--initial-branch=main"))
    _git(controlled, repository, ("config", "--local", "user.name", "Revanent Tests"))
    _git(controlled, repository, ("config", "--local", "user.email", "revanent@example.invalid"))
    _git(controlled, repository, ("config", "--local", "core.autocrlf", "false"))
    (repository / ".gitignore").write_text(".revanent/\n", encoding="utf-8")
    (repository / "tracked.txt").write_text("initial\n", encoding="utf-8")
    _git(controlled, repository, ("add", "--", ".gitignore", "tracked.txt"))
    _git(controlled, repository, ("commit", "-m", "initial"))
    return repository


def test_init_validate_and_doctor_are_safe_for_a_clean_repository(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    initialized = runner.invoke(app, ["init", "--repository", str(repository)])
    validated = runner.invoke(app, ["config", "validate", "--repository", str(repository)])
    doctor = runner.invoke(app, ["doctor", "--repository", str(repository), "--json"])

    assert initialized.exit_code == 0, initialized.output
    assert validated.exit_code == 0, validated.output
    assert doctor.exit_code == 0, doctor.output
    assert (repository / "revanent.yaml").is_file()
    assert (repository / ".revanent/worktrees").is_dir()
    assert "repository discovered" in doctor.output
    assert "invalid_configuration" not in doctor.output


def test_repeated_init_is_no_op_and_differing_config_refuses(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    first = runner.invoke(app, ["init", "--repository", str(repository)])
    config = repository / "revanent.yaml"
    original = config.read_bytes()
    second = runner.invoke(app, ["init", "--repository", str(repository)])
    config.write_text("user-owned: true\n", encoding="utf-8")
    conflict = runner.invoke(app, ["init", "--repository", str(repository)])

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert config.read_text(encoding="utf-8") == "user-owned: true\n"
    assert conflict.exit_code == 3
    assert "refused" in conflict.output
    assert original != config.read_bytes()


def test_explicit_repository_makes_validation_independent_of_caller_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path)
    initialized = runner.invoke(app, ["init", "--repository", str(repository)])
    other = tmp_path / "unrelated"
    other.mkdir()

    monkeypatch.chdir(other)
    result = runner.invoke(app, ["config", "validate", "--repository", str(repository)])

    assert initialized.exit_code == 0, initialized.output
    assert result.exit_code == 0, result.output


def test_agents_detect_reports_optional_opencode_absence_without_model_invocation(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)

    result = runner.invoke(app, ["agents", "detect", "--repository", str(repository), "--json"])
    strict = runner.invoke(app, ["agents", "detect", "--repository", str(repository), "--strict"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    providers = {provider["provider"]: provider for provider in payload["providers"]}
    assert providers["opencode"]["status"] == "UNAVAILABLE"
    assert providers["opencode"]["roles"] == {"builder": False, "repair": False, "review": False}
    assert providers["codex"]["roles"]["review"] is True
    assert strict.exit_code == 4


def test_init_supports_unicode_repository_paths(tmp_path: Path) -> None:
    repository = _repository(tmp_path, name="révanent 项目")

    result = runner.invoke(app, ["init", "--repository", str(repository)])

    assert result.exit_code == 0, result.output
    assert (repository / "revanent.yaml").is_file()
