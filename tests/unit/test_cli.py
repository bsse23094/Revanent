from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from revanent.application.doctor import DoctorCheck, DoctorResult, DoctorStatus
from revanent.application.runtime_composition import RuntimeDependencyError
from revanent.cli import app as cli_app
from revanent.cli.app import app
from revanent.config import render_default_config

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.startswith("revanent ")


def test_help_lists_only_implemented_p6_commands() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for command in ("init", "doctor", "run", "resume", "status", "cancel", "config", "agents"):
        assert command in result.stdout
    assert "| report" not in result.stdout
    assert "| clean" not in result.stdout


def test_runtime_configuration_failure_emits_no_traceback(tmp_path) -> None:  # type: ignore[no-untyped-def]
    result = runner.invoke(
        app,
        ["status", "run_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "--repository", str(tmp_path)],
    )

    assert result.exit_code == 2
    assert "Traceback" not in result.stdout


def test_run_missing_provider_dependency_exits_before_application_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "revanent.yaml").write_bytes(render_default_config("dependency-test"))
    task = tmp_path / "task.json"
    task.write_text(
        '{"schema_version":1,"id":"task_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        '"objective":"Bounded task.","allowed_paths":["src/**"],'
        '"acceptance_criteria":["Tests pass."]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cli_app,
        "load_effective_config",
        lambda repository: SimpleNamespace(repository_root=tmp_path),
    )

    def unavailable(effective: object) -> None:
        del effective
        raise RuntimeDependencyError

    monkeypatch.setattr(cli_app, "compose_runtime", unavailable)

    result = runner.invoke(
        app,
        ["run", "--repository", str(tmp_path), "--task-file", task.name],
    )

    assert result.exit_code == 4
    assert result.stdout == ""
    assert "Traceback" not in str(result.exception)


class _FakeDoctorService:
    def __init__(self, result: DoctorResult) -> None:
        self._result = result

    def run(self, *, repository_path: object, strict: bool) -> DoctorResult:
        del repository_path, strict
        return self._result


def test_doctor_allows_missing_optional_provider_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checks = (
        DoctorCheck("python", DoctorStatus.PASS, True, "CPython"),
        DoctorCheck("opencode", DoctorStatus.UNAVAILABLE, False, "not found"),
    )
    monkeypatch.setattr(
        cli_app, "DoctorService", lambda: _FakeDoctorService(DoctorResult(checks, 0))
    )

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "UNAVAILABLE" in result.stdout


def test_doctor_strict_rejects_missing_required_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    checks = (
        DoctorCheck("python", DoctorStatus.PASS, True, "CPython"),
        DoctorCheck("opencode", DoctorStatus.UNAVAILABLE, True, "not found"),
    )
    monkeypatch.setattr(
        cli_app, "DoctorService", lambda: _FakeDoctorService(DoctorResult(checks, 4))
    )

    result = runner.invoke(app, ["doctor", "--strict"])

    assert result.exit_code == 4


def test_doctor_json_is_the_only_stdout_document(monkeypatch: pytest.MonkeyPatch) -> None:
    checks = (DoctorCheck("python", DoctorStatus.PASS, True, "CPython"),)
    monkeypatch.setattr(
        cli_app, "DoctorService", lambda: _FakeDoctorService(DoctorResult(checks, 0))
    )

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "doctor"
    assert payload["checks"][0]["status"] == "PASS"
