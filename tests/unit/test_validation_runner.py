from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import pytest

from revanent.ports import (
    ArtifactReference,
    ArtifactStatus,
    CapturedOutput,
    CommandFailure,
    CommandFailureCategory,
    CommandRequest,
    CommandResult,
    CommandStatus,
    EnvironmentOverrides,
    EnvironmentVariable,
    OutputStream,
    ValidationCommand,
    ValidationCommandClass,
    ValidationEvidenceError,
    ValidationExecutionPolicy,
    ValidationOutputPolicy,
    ValidationStatus,
)
from revanent.validation import ValidationRunner, aggregate_validation_results
from tests.agent_factories import NOW
from tests.validation_factories import make_validation_command, make_validation_plan


@dataclass
class ScriptedCommandRunner:
    statuses: tuple[CommandStatus, ...]
    outputs: tuple[str, ...] = ()
    failure_category: CommandFailureCategory = CommandFailureCategory.LAUNCH
    requests: list[CommandRequest] = field(default_factory=list)

    def run(self, request: CommandRequest) -> CommandResult:
        index = len(self.requests)
        self.requests.append(request)
        status = self.statuses[index]
        text = self.outputs[index] if index < len(self.outputs) else ""
        started = NOW + timedelta(seconds=index + 1)
        failure = None
        exit_code: int | None = 0
        if status is CommandStatus.NONZERO_EXIT:
            exit_code = 7
        elif status not in {CommandStatus.SUCCESS, CommandStatus.NONZERO_EXIT}:
            exit_code = None
            category = (
                CommandFailureCategory.EXECUTABLE_UNAVAILABLE
                if status is CommandStatus.POLICY_REJECTED
                and self.failure_category is CommandFailureCategory.EXECUTABLE_UNAVAILABLE
                else self.failure_category
            )
            failure = CommandFailure(category=category, message="sanitized command failure")
        captured = CapturedOutput(
            text=text,
            observed_bytes=len(text.encode()),
            retained_bytes=len(text.encode()),
            truncated=False,
        )
        return CommandResult(
            correlation_id=request.correlation_id,
            executable=request.executable,
            resolved_executable=Path(__file__).resolve(),
            status=status,
            started_at=started,
            completed_at=started + timedelta(seconds=1),
            duration_seconds=1.0,
            stdout=captured,
            stderr=CapturedOutput(
                text="",
                observed_bytes=0,
                retained_bytes=0,
                truncated=False,
            ),
            exit_code=exit_code,
            failure=failure,
        )


class RaisingRunner:
    def run(self, request: CommandRequest) -> CommandResult:
        raise RuntimeError("raw command exception must not escape")


@dataclass
class OutputEvidenceRunner:
    stdout: CapturedOutput

    def run(self, request: CommandRequest) -> CommandResult:
        return CommandResult(
            correlation_id=request.correlation_id,
            executable=request.executable,
            resolved_executable=Path(__file__).resolve(),
            status=CommandStatus.SUCCESS,
            started_at=NOW + timedelta(seconds=1),
            completed_at=NOW + timedelta(seconds=2),
            duration_seconds=1.0,
            stdout=self.stdout,
            stderr=CapturedOutput(
                text="",
                observed_bytes=0,
                retained_bytes=0,
                truncated=False,
            ),
            exit_code=0,
        )


class CancellationAfter:
    def __init__(self, allowed_checks: int) -> None:
        self._allowed_checks = allowed_checks
        self._checks = 0

    def is_cancelled(self) -> bool:
        self._checks += 1
        return self._checks > self._allowed_checks


def _two_commands() -> tuple[ValidationCommand, ValidationCommand]:
    return (
        make_validation_command(),
        make_validation_command(
            "tests",
            command_id="vcmd_tests",
            arguments=("fixture", "exit", "0", "tests"),
        ),
    )


def test_all_required_commands_pass_in_declared_order(tmp_path: Path) -> None:
    runner = ScriptedCommandRunner((CommandStatus.SUCCESS, CommandStatus.SUCCESS))
    plan = make_validation_plan(tmp_path, commands=_two_commands())

    result = ValidationRunner(runner).execute(plan, started_at=NOW)

    assert result.status is ValidationStatus.PASSED
    assert result.required_commands_passed
    assert result.all_evidence_complete
    assert result.summary.passed == 2
    assert [request.arguments for request in runner.requests] == [
        plan.commands[0].arguments,
        plan.commands[1].arguments,
    ]
    assert [item.command_id for item in result.commands] == [
        plan.commands[0].id,
        plan.commands[1].id,
    ]


def test_stdout_claim_cannot_override_nonzero_exit(tmp_path: Path) -> None:
    runner = ScriptedCommandRunner(
        (CommandStatus.NONZERO_EXIT,), outputs=("all tests passed successfully",)
    )
    plan = make_validation_plan(tmp_path)

    result = ValidationRunner(runner).execute(plan, started_at=NOW)

    assert result.status is ValidationStatus.FAILED
    assert result.commands[0].status is ValidationStatus.FAILED
    assert result.commands[0].stdout.text == "all tests passed successfully"
    assert not result.required_commands_passed


@pytest.mark.parametrize(
    ("command_status", "failure_category", "validation_status"),
    [
        (CommandStatus.TIMEOUT, CommandFailureCategory.TIMEOUT, ValidationStatus.TIMED_OUT),
        (
            CommandStatus.CANCELLED,
            CommandFailureCategory.CANCELLATION,
            ValidationStatus.CANCELLED,
        ),
        (
            CommandStatus.POLICY_REJECTED,
            CommandFailureCategory.EXECUTABLE_UNAVAILABLE,
            ValidationStatus.UNAVAILABLE,
        ),
        (
            CommandStatus.POLICY_REJECTED,
            CommandFailureCategory.POLICY,
            ValidationStatus.BLOCKED,
        ),
        (
            CommandStatus.LAUNCH_FAILED,
            CommandFailureCategory.LAUNCH,
            ValidationStatus.BLOCKED,
        ),
        (
            CommandStatus.OUTPUT_ARTIFACT_FAILED,
            CommandFailureCategory.OUTPUT_ARTIFACT,
            ValidationStatus.INVALID,
        ),
        (
            CommandStatus.INTERNAL_ERROR,
            CommandFailureCategory.INTERNAL,
            ValidationStatus.INVALID,
        ),
    ],
)
def test_command_statuses_normalize_without_output_inference(
    command_status: CommandStatus,
    failure_category: CommandFailureCategory,
    validation_status: ValidationStatus,
    tmp_path: Path,
) -> None:
    runner = ScriptedCommandRunner((command_status,), failure_category=failure_category)

    result = ValidationRunner(runner).execute(make_validation_plan(tmp_path), started_at=NOW)

    assert result.status is validation_status
    assert result.commands[0].status is validation_status
    assert result.commands[0].failure is not None


def test_fail_fast_records_later_commands_as_not_run(tmp_path: Path) -> None:
    runner = ScriptedCommandRunner((CommandStatus.NONZERO_EXIT,))
    plan = make_validation_plan(tmp_path, commands=_two_commands(), fail_fast=True)

    result = ValidationRunner(runner).execute(plan, started_at=NOW)

    assert [item.status for item in result.commands] == [
        ValidationStatus.FAILED,
        ValidationStatus.NOT_RUN,
    ]
    assert result.status is ValidationStatus.FAILED
    assert not result.all_evidence_complete
    assert len(runner.requests) == 1


def test_explicit_advisory_failure_can_be_accepted(tmp_path: Path) -> None:
    commands = (
        make_validation_command(),
        make_validation_command(
            "advisory",
            command_id="vcmd_advisory",
            arguments=("fixture", "exit", "7"),
            classification=ValidationCommandClass.ADVISORY,
        ),
    )
    runner = ScriptedCommandRunner((CommandStatus.SUCCESS, CommandStatus.NONZERO_EXIT))
    plan = make_validation_plan(
        tmp_path,
        commands=commands,
        allow_advisory_failures=True,
    )

    result = ValidationRunner(runner).execute(plan, started_at=NOW)

    assert result.status is ValidationStatus.PASSED_WITH_ADVISORIES
    assert result.approvable
    assert result.advisory_failures_accepted
    assert result.summary.failed == 1


def test_advisory_failure_is_not_silently_reclassified(tmp_path: Path) -> None:
    commands = (
        make_validation_command(),
        make_validation_command(
            "advisory",
            command_id="vcmd_advisory",
            arguments=("fixture", "exit", "7"),
            classification=ValidationCommandClass.ADVISORY,
        ),
    )
    runner = ScriptedCommandRunner((CommandStatus.SUCCESS, CommandStatus.NONZERO_EXIT))

    result = ValidationRunner(runner).execute(
        make_validation_plan(tmp_path, commands=commands), started_at=NOW
    )

    assert result.status is ValidationStatus.FAILED
    assert not result.advisory_failures_accepted


def test_precancelled_plan_launches_no_command(tmp_path: Path) -> None:
    runner = ScriptedCommandRunner((CommandStatus.SUCCESS,))
    plan = make_validation_plan(tmp_path, commands=_two_commands())

    result = ValidationRunner(runner).execute(
        plan,
        started_at=NOW,
        cancellation=CancellationAfter(0),
    )

    assert [item.status for item in result.commands] == [
        ValidationStatus.CANCELLED,
        ValidationStatus.NOT_RUN,
    ]
    assert result.status is ValidationStatus.CANCELLED
    assert runner.requests == []


def test_cancellation_between_commands_prevents_later_launch(tmp_path: Path) -> None:
    runner = ScriptedCommandRunner((CommandStatus.SUCCESS,))
    plan = make_validation_plan(tmp_path, commands=_two_commands())

    result = ValidationRunner(runner).execute(
        plan,
        started_at=NOW,
        cancellation=CancellationAfter(1),
    )

    assert [item.status for item in result.commands] == [
        ValidationStatus.PASSED,
        ValidationStatus.CANCELLED,
    ]
    assert len(runner.requests) == 1


def test_inflight_cancellation_stops_plan_without_fail_fast(tmp_path: Path) -> None:
    runner = ScriptedCommandRunner(
        (CommandStatus.CANCELLED,),
        failure_category=CommandFailureCategory.CANCELLATION,
    )

    result = ValidationRunner(runner).execute(
        make_validation_plan(tmp_path, commands=_two_commands()),
        started_at=NOW,
    )

    assert [item.status for item in result.commands] == [
        ValidationStatus.CANCELLED,
        ValidationStatus.NOT_RUN,
    ]
    assert len(runner.requests) == 1


def test_unknown_environment_name_fails_preflight_without_launch(tmp_path: Path) -> None:
    runner = ScriptedCommandRunner((CommandStatus.SUCCESS,))
    environment = EnvironmentOverrides((EnvironmentVariable("UNDECLARED", "value"),))

    result = ValidationRunner(runner).execute(
        make_validation_plan(tmp_path),
        started_at=NOW,
        environment=environment,
    )

    assert result.status is ValidationStatus.INVALID
    assert result.commands[0].failure is not None
    assert result.commands[0].failure.code == "environment_not_authorized"
    assert runner.requests == []


def test_runner_exception_is_sanitized_as_invalid_evidence(tmp_path: Path) -> None:
    result = ValidationRunner(RaisingRunner()).execute(
        make_validation_plan(tmp_path), started_at=NOW
    )

    assert result.status is ValidationStatus.INVALID
    assert result.commands[0].failure is not None
    assert result.commands[0].failure.code == "command_boundary_exception"
    assert "raw command exception" not in result.model_dump_json()


def test_artifact_path_escape_is_invalid_and_not_disclosed(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    escaped_artifact = tmp_path / "outside.log"
    escaped_artifact.write_text("safe", encoding="utf-8")
    output = CapturedOutput(
        text="sa",
        observed_bytes=4,
        retained_bytes=2,
        truncated=True,
        artifact=ArtifactReference(
            path=escaped_artifact.resolve(strict=True),
            stream=OutputStream.STDOUT,
            status=ArtifactStatus.COMPLETE,
            observed_bytes=4,
            source_bytes_retained=4,
            redacted_bytes_observed=4,
            stored_bytes=4,
        ),
    )
    command = make_validation_command().model_copy(
        update={"output": ValidationOutputPolicy(capture_artifacts=True)}
    )

    result = ValidationRunner(OutputEvidenceRunner(output)).execute(
        make_validation_plan(
            tmp_path,
            commands=(command,),
            artifact_directory=artifact_root,
        ),
        started_at=NOW,
    )

    assert result.status is ValidationStatus.INVALID
    assert result.commands[0].failure is not None
    assert result.commands[0].failure.code == "invalid_command_output_evidence"
    assert str(escaped_artifact) not in result.model_dump_json()


def test_required_complete_output_rejects_truncation_without_artifact(
    tmp_path: Path,
) -> None:
    output = CapturedOutput(
        text="sa",
        observed_bytes=4,
        retained_bytes=2,
        truncated=True,
    )
    command = make_validation_command().model_copy(
        update={"output": ValidationOutputPolicy(require_complete_stdout=True)}
    )

    result = ValidationRunner(OutputEvidenceRunner(output)).execute(
        make_validation_plan(tmp_path, commands=(command,)),
        started_at=NOW,
    )

    assert result.status is ValidationStatus.INVALID
    assert result.commands[0].failure is not None
    assert result.commands[0].failure.code == "invalid_command_output_evidence"


def test_missing_extra_or_out_of_order_results_fail_closed(tmp_path: Path) -> None:
    runner = ScriptedCommandRunner((CommandStatus.SUCCESS, CommandStatus.SUCCESS))
    plan = make_validation_plan(tmp_path, commands=_two_commands())
    result = ValidationRunner(runner).execute(plan, started_at=NOW)

    with pytest.raises(ValidationEvidenceError, match="count"):
        aggregate_validation_results(plan, result.commands[:1])
    with pytest.raises(ValidationEvidenceError, match="order"):
        aggregate_validation_results(plan, tuple(reversed(result.commands)))
    with pytest.raises(ValidationEvidenceError, match="count"):
        aggregate_validation_results(plan, (*result.commands, result.commands[-1]))


def test_plan_policy_is_preserved_and_never_modified_by_runner(tmp_path: Path) -> None:
    plan = make_validation_plan(tmp_path)
    original = plan.execution
    runner = ScriptedCommandRunner((CommandStatus.SUCCESS,))

    ValidationRunner(runner).execute(plan, started_at=NOW)

    assert plan.execution == original == ValidationExecutionPolicy()
