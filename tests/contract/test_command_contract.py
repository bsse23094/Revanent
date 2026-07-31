from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path

import pytest

from revanent.ports import (
    CapturedOutput,
    CommandRequest,
    CommandResult,
    CommandStatus,
    EnvironmentOverrides,
    OutputLimits,
)


def _request(**changes: object) -> CommandRequest:
    values: dict[str, object] = {
        "executable": "fixture-python",
        "arguments": ("fixture.py", "args"),
        "working_directory": Path.cwd(),
        "correlation_id": "command-contract",
    }
    values.update(changes)
    if isinstance(values.get("environment"), dict):
        values["environment"] = EnvironmentOverrides.from_mapping(values["environment"])  # type: ignore[arg-type]
    return CommandRequest(**values)  # type: ignore[arg-type]


def test_command_request_version_1_is_immutable_and_hides_sensitive_fields() -> None:
    request = _request(arguments=("secret-value",), environment={"SAFE": "secret-value"})

    assert request.schema_version == 1
    assert "secret-value" not in repr(request)
    with pytest.raises(FrozenInstanceError):
        request.executable = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        request.environment.variables = ()  # type: ignore[misc]
    with pytest.raises(TypeError, match="EnvironmentOverrides"):
        CommandRequest(
            executable="tool",
            arguments=(),
            working_directory=Path.cwd(),
            correlation_id="raw-environment-rejected",
            environment={"SAFE": "value"},  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("executable", ""),
        ("executable", "bad\x00name"),
        ("arguments", ("bad\x00argument",)),
        ("correlation_id", "../escape"),
        ("timeout_seconds", 0),
        ("timeout_seconds", True),
        ("timeout_seconds", float("nan")),
        ("timeout_seconds", 86_401),
        ("stdin", "not-bytes"),
        ("expected_exit_codes", ()),
        ("expected_exit_codes", (0, 0)),
        ("environment", {"BAD=KEY": "value"}),
        ("environment", {"SAFE": "bad\x00value"}),
        ("schema_version", 2),
    ],
)
def test_command_request_rejects_malformed_or_unbounded_values(field: str, value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        _request(**{field: value})


def test_output_limits_are_positive_and_bounded() -> None:
    with pytest.raises(ValueError):
        OutputLimits(stdout_bytes=0)
    with pytest.raises(ValueError):
        OutputLimits(stderr_bytes=16 * 1_024 * 1_024 + 1)
    with pytest.raises(ValueError):
        OutputLimits(artifact_bytes_per_stream=64 * 1_024 * 1_024 + 1)
    with pytest.raises(ValueError):
        OutputLimits(stdout_bytes=True)


def test_command_result_rejects_invalid_time_and_status_combinations() -> None:
    now = datetime(2026, 7, 30, tzinfo=UTC)
    output = CapturedOutput(text="", observed_bytes=0, retained_bytes=0, truncated=False)
    values: dict[str, object] = {
        "correlation_id": "result-contract",
        "executable": "tool",
        "resolved_executable": Path.cwd().resolve(),
        "status": CommandStatus.SUCCESS,
        "started_at": now,
        "completed_at": now,
        "duration_seconds": 0.0,
        "stdout": output,
        "stderr": output,
        "exit_code": 0,
    }

    assert CommandResult(**values).succeeded is True  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="timestamps"):
        CommandResult(**(values | {"started_at": now.replace(tzinfo=None)}))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="exit code"):
        CommandResult(**(values | {"exit_code": None}))  # type: ignore[arg-type]


def test_command_status_wire_values_are_stable() -> None:
    assert [status.value for status in CommandStatus] == [
        "SUCCESS",
        "NONZERO_EXIT",
        "TIMEOUT",
        "CANCELLED",
        "LAUNCH_FAILED",
        "POLICY_REJECTED",
        "OUTPUT_ARTIFACT_FAILED",
        "INTERNAL_ERROR",
    ]
