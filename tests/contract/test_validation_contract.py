"""Version-1 validation evidence contract tests."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from revanent.ports import (
    CapturedOutput,
    CommandRequest,
    CommandResult,
    CommandStatus,
    RepositoryPath,
    ValidationCommand,
    ValidationCommandClass,
    ValidationCommandId,
    ValidationOutputPolicy,
    ValidationPlan,
    ValidationPlanResult,
    canonical_validation_bytes,
)
from revanent.validation import ValidationRunner
from tests.agent_factories import NOW
from tests.validation_factories import make_validation_command, make_validation_plan


class PassingRunner:
    def run(self, request: CommandRequest) -> CommandResult:
        return CommandResult(
            correlation_id=request.correlation_id,
            executable=request.executable,
            resolved_executable=Path(__file__).resolve(),
            status=CommandStatus.SUCCESS,
            started_at=NOW + timedelta(seconds=1),
            completed_at=NOW + timedelta(seconds=2),
            duration_seconds=1.0,
            stdout=CapturedOutput(text="ok", observed_bytes=2, retained_bytes=2, truncated=False),
            stderr=CapturedOutput(text="", observed_bytes=0, retained_bytes=0, truncated=False),
            exit_code=0,
        )


def test_validation_plan_and_result_version_one_round_trip(tmp_path: Path) -> None:
    plan = make_validation_plan(tmp_path)
    result = ValidationRunner(PassingRunner()).execute(plan, started_at=NOW)

    assert ValidationPlan.model_validate_json(plan.model_dump_json()) == plan
    assert ValidationPlanResult.model_validate_json(result.model_dump_json()) == result


def test_validation_canonical_serialization_is_stable(tmp_path: Path) -> None:
    plan = make_validation_plan(tmp_path)
    result = ValidationRunner(PassingRunner()).execute(plan, started_at=NOW)

    for model in (plan, result):
        encoded = canonical_validation_bytes(model)
        assert encoded == canonical_validation_bytes(type(model).model_validate_json(encoded))
        assert (
            encoded
            == json.dumps(
                json.loads(encoded),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )


@pytest.mark.parametrize("model_name", ["plan", "result"])
def test_validation_contracts_are_immutable(model_name: str, tmp_path: Path) -> None:
    plan = make_validation_plan(tmp_path)
    model = (
        plan
        if model_name == "plan"
        else ValidationRunner(PassingRunner()).execute(plan, started_at=NOW)
    )
    with pytest.raises(ValidationError):
        model.schema_version = 2  # type: ignore[assignment]


@pytest.mark.parametrize("model_name", ["plan", "result"])
def test_unknown_fields_and_schema_versions_are_rejected(model_name: str, tmp_path: Path) -> None:
    plan = make_validation_plan(tmp_path)
    model = (
        plan
        if model_name == "plan"
        else ValidationRunner(PassingRunner()).execute(plan, started_at=NOW)
    )
    model_type = type(model)
    data = model.model_dump(mode="json")
    data["unexpected"] = True
    with pytest.raises(ValidationError):
        model_type.model_validate(data)
    data.pop("unexpected")
    data["schema_version"] = 2
    with pytest.raises(ValidationError):
        model_type.model_validate(data)


@pytest.mark.parametrize("duplicate", ["id", "name", "signature"])
def test_duplicate_plan_commands_are_rejected(duplicate: str, tmp_path: Path) -> None:
    first = make_validation_command()
    if duplicate == "id":
        second = make_validation_command(
            "tests",
            command_id=str(first.id),
            arguments=("fixture", "exit", "1"),
        )
    elif duplicate == "name":
        second = make_validation_command(
            first.name,
            command_id="vcmd_tests",
            arguments=("fixture", "exit", "1"),
        )
    else:
        second = make_validation_command("tests", command_id="vcmd_tests")
    with pytest.raises(ValidationError, match=r"unique|duplicate"):
        make_validation_plan(tmp_path, commands=(first, second))


@pytest.mark.parametrize(
    "executable", ["../python", "C:/python.exe", "python\\tool", "--python", ""]
)
def test_executable_paths_and_option_like_names_are_rejected(executable: str) -> None:
    with pytest.raises(ValidationError):
        make_validation_command(executable=executable)


@pytest.mark.parametrize(
    "arguments",
    [
        ("valid", "bad\x00value"),
        ("--api-key=secret-value",),
        ("Authorization: bearer value",),
        ("password=hunter2",),
    ],
)
def test_nulls_and_credential_assignments_are_rejected(arguments: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError, match=r"credential|null"):
        make_validation_command(arguments=arguments)


def test_security_critical_command_cannot_be_advisory() -> None:
    command = make_validation_command(classification=ValidationCommandClass.ADVISORY)
    data = command.model_dump()
    data["security_critical"] = True
    with pytest.raises(ValidationError, match="security-critical"):
        ValidationCommand.model_validate(data)


def test_plan_requires_at_least_one_required_command(tmp_path: Path) -> None:
    advisory = make_validation_command(classification=ValidationCommandClass.ADVISORY)
    with pytest.raises(ValidationError, match="required command"):
        make_validation_plan(tmp_path, commands=(advisory,))


def test_expected_exit_and_environment_names_are_canonical() -> None:
    base = make_validation_command().model_dump()
    with pytest.raises(ValidationError, match="exit codes"):
        ValidationCommand.model_validate({**base, "expected_exit_codes": (1, 0)})
    with pytest.raises(ValidationError, match="environment names"):
        ValidationCommand.model_validate({**base, "allowed_environment_names": ("ZED", "ALPHA")})


def test_timeout_and_output_bounds_are_enforced() -> None:
    data = make_validation_command().model_dump()
    with pytest.raises(ValidationError):
        ValidationCommand.model_validate({**data, "timeout_seconds": 0})
    with pytest.raises(ValidationError):
        ValidationOutputPolicy(stdout_bytes=1_048_577)


@pytest.mark.parametrize("path", ["../escape", "/absolute", "C:/absolute", "a/../b"])
def test_relative_working_directories_cannot_escape(path: str) -> None:
    with pytest.raises(ValidationError):
        ValidationCommand(
            id=ValidationCommandId("vcmd_path"),
            name="path check",
            executable="fixture-python",
            relative_working_directory=RepositoryPath(path),
        )


def test_plan_order_is_stable_and_not_canonicalized(tmp_path: Path) -> None:
    first = make_validation_command()
    second = make_validation_command(
        "tests",
        command_id="vcmd_tests",
        arguments=("fixture", "exit", "0", "tests"),
    )

    plan = make_validation_plan(tmp_path, commands=(second, first))

    assert plan.commands == (second, first)


def test_command_argument_boundary_remains_separate(tmp_path: Path) -> None:
    command = ValidationCommand(
        id=ValidationCommandId("vcmd_literal"),
        name="literal arguments",
        executable="fixture-python",
        arguments=("with spaces", "; not a shell", "&& literal"),
    )
    plan = make_validation_plan(tmp_path, commands=(command,))
    assert plan.commands[0].executable == "fixture-python"
    assert plan.commands[0].arguments == ("with spaces", "; not a shell", "&& literal")
