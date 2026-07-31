from __future__ import annotations

from datetime import UTC, datetime

from revanent.ports import (
    CapturedOutput,
    CommandFailure,
    CommandFailureCategory,
    CommandRequest,
    CommandResult,
    CommandStatus,
)
from revanent.utilities import tool_detection
from revanent.utilities.tool_detection import CheckStatus

NOW = datetime(2026, 7, 30, tzinfo=UTC)


def _output(text: str = "") -> CapturedOutput:
    size = len(text.encode())
    return CapturedOutput(text=text, observed_bytes=size, retained_bytes=size, truncated=False)


class FakeRunner:
    def __init__(self, result: CommandResult) -> None:
        self.result = result
        self.requests: list[CommandRequest] = []

    def run(self, request: CommandRequest) -> CommandResult:
        self.requests.append(request)
        return self.result


def _result(
    status: CommandStatus,
    *,
    stdout: str = "",
    failure: CommandFailure | None = None,
) -> CommandResult:
    return CommandResult(
        correlation_id="doctor-sample",
        executable="sample",
        resolved_executable=None,
        status=status,
        started_at=NOW,
        completed_at=NOW,
        duration_seconds=0,
        stdout=_output(stdout),
        stderr=_output(),
        exit_code=0 if status is CommandStatus.SUCCESS else None,
        failure=failure,
    )


def test_probe_reports_missing_executable() -> None:
    runner = FakeRunner(
        _result(
            CommandStatus.POLICY_REJECTED,
            failure=CommandFailure(
                CommandFailureCategory.EXECUTABLE_UNAVAILABLE,
                "authorized executable is unavailable",
            ),
        )
    )

    check = tool_detection._probe(
        runner,
        "missing",
        ("--version",),
        required=False,
        category="provider",
    )

    assert check.status is CheckStatus.UNAVAILABLE
    assert check.detail == "not found on configured PATH"


def test_probe_uses_typed_argument_list_and_captures_version() -> None:
    runner = FakeRunner(_result(CommandStatus.SUCCESS, stdout="sample 1.2.3\n"))

    check = tool_detection._probe(
        runner,
        "sample",
        ("--version",),
        required=True,
        category="runtime",
    )

    assert len(runner.requests) == 1
    assert runner.requests[0].executable == "sample"
    assert runner.requests[0].arguments == ("--version",)
    assert check.status is CheckStatus.AVAILABLE
    assert check.detail == "sample 1.2.3"


def test_detect_environment_has_required_and_optional_checks() -> None:
    checks = {check.name: check for check in tool_detection.detect_environment()}

    assert checks["python"].required is True
    assert checks["git"].required is True
    assert checks["opencode"].required is False
    assert checks["codex"].category == "provider"
