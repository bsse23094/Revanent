"""Deterministic ordered validation through the controlled-command port."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from revanent.ports.agents import RepositoryPath
from revanent.ports.commands import (
    ArtifactReference,
    ArtifactStatus,
    CancellationToken,
    CapturedOutput,
    CommandFailureCategory,
    CommandRequest,
    CommandResult,
    CommandRunner,
    CommandStatus,
    EnvironmentOverrides,
    OutputLimits,
)
from revanent.ports.validation import (
    ValidationArtifactReference,
    ValidationCapturedOutput,
    ValidationCommand,
    ValidationCommandClass,
    ValidationCommandResult,
    ValidationEvidenceError,
    ValidationFailure,
    ValidationFailureCategory,
    ValidationPlan,
    ValidationPlanResult,
    ValidationStatus,
    validation_correlation_id,
    validation_summary,
)


class ValidationRunner:
    """Execute a validated plan in declaration order without retries or state mutation."""

    def __init__(self, command_runner: CommandRunner) -> None:
        self._command_runner = command_runner

    def execute(
        self,
        plan: ValidationPlan,
        *,
        started_at: datetime,
        cancellation: CancellationToken | None = None,
        environment: EnvironmentOverrides | None = None,
    ) -> ValidationPlanResult:
        if not isinstance(plan, ValidationPlan):
            raise TypeError("plan must be a validated ValidationPlan")
        _require_utc(started_at)
        selected_environment = environment or EnvironmentOverrides()
        preflight = self._preflight(plan, selected_environment)
        results: list[ValidationCommandResult] = []
        cursor = started_at
        if preflight is not None:
            results.append(
                _prelaunch_result(
                    plan,
                    plan.commands[0],
                    sequence=1,
                    occurred_at=cursor,
                    status=ValidationStatus.INVALID,
                    failure=preflight,
                )
            )
            results.extend(
                _not_run_results(
                    plan,
                    start_sequence=2,
                    occurred_at=cursor,
                    code="plan_preflight_failed",
                    message="validation command was not run after plan preflight failed",
                )
            )
            return aggregate_validation_results(plan, tuple(results))

        for sequence, command in enumerate(plan.commands, start=1):
            if cancellation is not None and cancellation.is_cancelled():
                results.append(
                    _prelaunch_result(
                        plan,
                        command,
                        sequence=sequence,
                        occurred_at=cursor,
                        status=ValidationStatus.CANCELLED,
                        failure=_failure(
                            ValidationFailureCategory.CANCELLATION,
                            "cancelled_before_command",
                            "validation was cancelled before command launch",
                        ),
                    )
                )
                results.extend(
                    _not_run_results(
                        plan,
                        start_sequence=sequence + 1,
                        occurred_at=cursor,
                        code="plan_cancelled",
                        message="validation command was not run after cancellation",
                    )
                )
                break
            result = self._execute_command(
                plan,
                command,
                sequence=sequence,
                cancellation=cancellation,
                environment=selected_environment,
                fallback_time=cursor,
            )
            results.append(result)
            cursor = result.completed_at
            if _should_stop(plan, result):
                cancelled = result.status is ValidationStatus.CANCELLED
                results.extend(
                    _not_run_results(
                        plan,
                        start_sequence=sequence + 1,
                        occurred_at=cursor,
                        code="plan_cancelled" if cancelled else "fail_fast",
                        message=(
                            "validation command was not run after cancellation"
                            if cancelled
                            else "validation command was not run because fail-fast stopped the plan"
                        ),
                    )
                )
                break
        return aggregate_validation_results(plan, tuple(results))

    def _preflight(
        self, plan: ValidationPlan, environment: EnvironmentOverrides
    ) -> ValidationFailure | None:
        try:
            root = plan.workspace.root.resolve(strict=True)
        except OSError:
            return _failure(
                ValidationFailureCategory.INVALID_EVIDENCE,
                "workspace_unavailable",
                "validation workspace does not exist or cannot be resolved",
            )
        if not root.is_dir():
            return _failure(
                ValidationFailureCategory.INVALID_EVIDENCE,
                "workspace_invalid",
                "validation workspace must be an existing directory",
            )
        allowed_names = {
            name for command in plan.commands for name in command.allowed_environment_names
        }
        if any(variable.key not in allowed_names for variable in environment.variables):
            return _failure(
                ValidationFailureCategory.INVALID_EVIDENCE,
                "environment_not_authorized",
                "validation environment contains a name not authorized by the plan",
            )
        if plan.artifacts.directory is not None:
            try:
                artifact_directory = plan.artifacts.directory.resolve(strict=True)
            except OSError:
                return _failure(
                    ValidationFailureCategory.INVALID_EVIDENCE,
                    "artifact_directory_unavailable",
                    "validation artifact directory does not exist or cannot be resolved",
                )
            if not artifact_directory.is_dir():
                return _failure(
                    ValidationFailureCategory.INVALID_EVIDENCE,
                    "artifact_directory_invalid",
                    "validation artifact directory must be an existing directory",
                )
        for command in plan.commands:
            failure = _resolve_working_directory(root, command)[1]
            if failure is not None:
                return failure
        return None

    def _execute_command(
        self,
        plan: ValidationPlan,
        command: ValidationCommand,
        *,
        sequence: int,
        cancellation: CancellationToken | None,
        environment: EnvironmentOverrides,
        fallback_time: datetime,
    ) -> ValidationCommandResult:
        root = plan.workspace.root.resolve(strict=True)
        working_directory, path_failure = _resolve_working_directory(root, command)
        if path_failure is not None or working_directory is None:
            return _prelaunch_result(
                plan,
                command,
                sequence=sequence,
                occurred_at=fallback_time,
                status=ValidationStatus.INVALID,
                failure=path_failure
                or _failure(
                    ValidationFailureCategory.INVALID_EVIDENCE,
                    "working_directory_invalid",
                    "validation working directory could not be resolved",
                ),
            )
        allowed = set(command.allowed_environment_names)
        command_environment = EnvironmentOverrides(
            tuple(variable for variable in environment.variables if variable.key in allowed)
        )
        correlation_id = validation_correlation_id(plan.id, command.id)
        artifact_directory = plan.artifacts.directory if command.output.capture_artifacts else None
        try:
            request = CommandRequest(
                executable=command.executable,
                arguments=command.arguments,
                working_directory=working_directory,
                correlation_id=correlation_id,
                environment=command_environment,
                timeout_seconds=command.timeout_seconds,
                output_limits=OutputLimits(
                    stdout_bytes=command.output.stdout_bytes,
                    stderr_bytes=command.output.stderr_bytes,
                    artifact_bytes_per_stream=command.output.artifact_bytes_per_stream,
                ),
                cancellation=cancellation,
                expected_exit_codes=command.expected_exit_codes,
                artifact_directory=artifact_directory,
            )
            result = self._command_runner.run(request)
        except Exception:
            return _prelaunch_result(
                plan,
                command,
                sequence=sequence,
                occurred_at=fallback_time,
                status=ValidationStatus.INVALID,
                failure=_failure(
                    ValidationFailureCategory.INTERNAL,
                    "command_boundary_exception",
                    "validation command boundary raised an unexpected exception",
                ),
            )
        return _normalize_command_result(
            plan,
            command,
            sequence=sequence,
            result=result,
            earliest_start=fallback_time,
        )


def aggregate_validation_results(
    plan: ValidationPlan,
    results: tuple[ValidationCommandResult, ...],
) -> ValidationPlanResult:
    """Validate exact plan/result correlation and compute one deterministic aggregate."""
    if len(results) != len(plan.commands):
        raise ValidationEvidenceError("validation result count does not match the plan")
    previous_completed: datetime | None = None
    for sequence, (command, result) in enumerate(zip(plan.commands, results, strict=True), start=1):
        expected_correlation = validation_correlation_id(plan.id, command.id)
        if (
            result.sequence != sequence
            or result.command_id != command.id
            or result.plan_id != plan.id
            or result.run_id != plan.run_id
            or result.work_package_id != plan.work_package_id
            or result.classification is not command.classification
            or result.executable != command.executable
            or result.expected_exit_codes != command.expected_exit_codes
            or result.correlation_id != expected_correlation
        ):
            raise ValidationEvidenceError("validation command evidence does not match plan order")
        if result.started_at < plan.created_at:
            raise ValidationEvidenceError("validation command evidence predates its plan")
        if previous_completed is not None and result.started_at < previous_completed:
            raise ValidationEvidenceError("validation command evidence overlaps or is out of order")
        previous_completed = result.completed_at

    required_passed = all(
        result.status is ValidationStatus.PASSED
        for command, result in zip(plan.commands, results, strict=True)
        if command.classification is ValidationCommandClass.REQUIRED
    )
    incomplete = {
        ValidationStatus.TIMED_OUT,
        ValidationStatus.CANCELLED,
        ValidationStatus.BLOCKED,
        ValidationStatus.UNAVAILABLE,
        ValidationStatus.INVALID,
        ValidationStatus.NOT_RUN,
    }
    all_complete = all(result.status not in incomplete for result in results)
    advisory_failures = tuple(
        result
        for command, result in zip(plan.commands, results, strict=True)
        if command.classification is ValidationCommandClass.ADVISORY
        and result.status is ValidationStatus.FAILED
    )
    only_allowed_outcomes = all(
        result.status is ValidationStatus.PASSED
        or (
            command.classification is ValidationCommandClass.ADVISORY
            and result.status is ValidationStatus.FAILED
            and plan.execution.allow_advisory_failures
        )
        for command, result in zip(plan.commands, results, strict=True)
    )
    advisory_accepted = bool(advisory_failures) and (
        plan.execution.allow_advisory_failures
        and required_passed
        and all_complete
        and only_allowed_outcomes
    )
    if required_passed and all_complete and only_allowed_outcomes:
        status = (
            ValidationStatus.PASSED_WITH_ADVISORIES
            if advisory_accepted
            else ValidationStatus.PASSED
        )
    else:
        status = _aggregate_failure_status(results)
    started_at = min(result.started_at for result in results)
    completed_at = max(result.completed_at for result in results)
    return ValidationPlanResult(
        plan_id=plan.id,
        run_id=plan.run_id,
        work_package_id=plan.work_package_id,
        started_at=started_at,
        completed_at=completed_at,
        status=status,
        commands=results,
        summary=validation_summary(results),
        required_commands_passed=required_passed,
        all_evidence_complete=all_complete,
        advisory_failures_accepted=advisory_accepted,
        cancelled=any(result.status is ValidationStatus.CANCELLED for result in results),
    )


def _normalize_command_result(
    plan: ValidationPlan,
    command: ValidationCommand,
    *,
    sequence: int,
    result: CommandResult,
    earliest_start: datetime,
) -> ValidationCommandResult:
    correlation_id = validation_correlation_id(plan.id, command.id)
    if result.started_at < earliest_start:
        return _invalid_result_from_command(
            plan,
            command,
            sequence=sequence,
            result=result,
            code="command_chronology_invalid",
            message="command result chronology did not follow validation plan order",
            occurred_at=earliest_start,
        )
    duration_ms = _duration_ms(result.started_at, result.completed_at)
    completed_at = result.started_at + timedelta(milliseconds=duration_ms)
    if result.correlation_id != correlation_id or result.executable != command.executable:
        return _invalid_result_from_command(
            plan,
            command,
            sequence=sequence,
            result=result,
            code="command_correlation_mismatch",
            message="command result correlation did not match the validation plan",
        )
    identity_required = result.status in {
        CommandStatus.SUCCESS,
        CommandStatus.NONZERO_EXIT,
        CommandStatus.TIMEOUT,
        CommandStatus.CANCELLED,
        CommandStatus.LAUNCH_FAILED,
        CommandStatus.OUTPUT_ARTIFACT_FAILED,
    }
    if identity_required and (
        result.resolved_executable is None or not result.resolved_executable.is_absolute()
    ):
        return _invalid_result_from_command(
            plan,
            command,
            sequence=sequence,
            result=result,
            code="executable_identity_missing",
            message="attempted validation command omitted a trusted executable identity",
        )
    try:
        stdout = _translate_output(
            plan,
            command,
            result.stdout,
            stream_name="stdout",
            correlation_id=correlation_id,
        )
        stderr = _translate_output(
            plan,
            command,
            result.stderr,
            stream_name="stderr",
            correlation_id=correlation_id,
        )
        _require_output_completeness(command, stdout, stderr)
    except ValueError:
        return _invalid_result_from_command(
            plan,
            command,
            sequence=sequence,
            result=result,
            code="invalid_command_output_evidence",
            message="command output or artifact evidence failed strict validation",
        )
    status, failure = _map_command_status(command, result)
    return ValidationCommandResult(
        plan_id=plan.id,
        run_id=plan.run_id,
        work_package_id=plan.work_package_id,
        command_id=command.id,
        sequence=sequence,
        classification=command.classification,
        status=status,
        command_status=result.status,
        started_at=result.started_at,
        completed_at=completed_at,
        duration_ms=duration_ms,
        executable=command.executable,
        resolved_executable=result.resolved_executable,
        correlation_id=correlation_id,
        exit_code=result.exit_code,
        expected_exit_codes=command.expected_exit_codes,
        stdout=stdout,
        stderr=stderr,
        failure=failure,
    )


def _map_command_status(
    command: ValidationCommand,
    result: CommandResult,
) -> tuple[ValidationStatus, ValidationFailure | None]:
    if result.status is CommandStatus.SUCCESS:
        if result.exit_code not in command.expected_exit_codes:
            return ValidationStatus.INVALID, _failure(
                ValidationFailureCategory.INVALID_EVIDENCE,
                "expected_exit_mismatch",
                "successful command result carried an unexpected exit code",
            )
        return ValidationStatus.PASSED, None
    if result.status is CommandStatus.NONZERO_EXIT:
        return ValidationStatus.FAILED, _failure(
            ValidationFailureCategory.COMMAND_FAILED,
            "unexpected_exit_code",
            "validation command returned an unexpected exit code",
        )
    if result.status is CommandStatus.TIMEOUT:
        return ValidationStatus.TIMED_OUT, _failure(
            ValidationFailureCategory.TIMEOUT,
            "command_timeout",
            "validation command exceeded its timeout",
            result,
        )
    if result.status is CommandStatus.CANCELLED:
        return ValidationStatus.CANCELLED, _failure(
            ValidationFailureCategory.CANCELLATION,
            "command_cancelled",
            "validation command was cancelled",
            result,
        )
    if result.status is CommandStatus.POLICY_REJECTED:
        if (
            result.failure is not None
            and result.failure.category is CommandFailureCategory.EXECUTABLE_UNAVAILABLE
        ):
            return ValidationStatus.UNAVAILABLE, _failure(
                ValidationFailureCategory.EXECUTABLE_UNAVAILABLE,
                "executable_unavailable",
                "validation executable is unavailable under command policy",
                result,
            )
        return ValidationStatus.BLOCKED, _failure(
            ValidationFailureCategory.POLICY_BLOCKED,
            "command_policy_blocked",
            "validation command was blocked by controlled-command policy",
            result,
        )
    if result.status is CommandStatus.LAUNCH_FAILED:
        return ValidationStatus.BLOCKED, _failure(
            ValidationFailureCategory.LAUNCH_BLOCKED,
            "command_launch_failed",
            "validation command could not be launched",
            result,
        )
    if result.status is CommandStatus.OUTPUT_ARTIFACT_FAILED:
        return ValidationStatus.INVALID, _failure(
            ValidationFailureCategory.ARTIFACT,
            "command_artifact_failed",
            "validation command artifact evidence could not be retained",
            result,
        )
    return ValidationStatus.INVALID, _failure(
        ValidationFailureCategory.INTERNAL,
        "command_internal_error",
        "controlled validation command failed internally",
        result,
    )


def _translate_output(
    plan: ValidationPlan,
    command: ValidationCommand,
    output: CapturedOutput,
    *,
    stream_name: str,
    correlation_id: str,
) -> ValidationCapturedOutput:
    limit = command.output.stdout_bytes if stream_name == "stdout" else command.output.stderr_bytes
    # The controlled runner may append bounded truncation/redaction diagnostics after
    # retaining ``limit`` source bytes. Keep that representation overhead finite too.
    if output.retained_bytes > limit or len(output.text.encode("utf-8")) > limit + 256:
        raise ValidationEvidenceError("captured command output exceeded the validation limit")
    if "\x00" in output.text:
        raise ValidationEvidenceError("captured command output contained a null byte")
    artifact = None
    if output.artifact is not None:
        if not command.output.capture_artifacts or plan.artifacts.directory is None:
            raise ValidationEvidenceError("unexpected command artifact reference")
        if output.artifact.stream.value != stream_name:
            raise ValidationEvidenceError("command artifact stream did not match captured output")
        artifact = _translate_artifact(
            plan,
            output.artifact,
            correlation_id=correlation_id,
        )
    return ValidationCapturedOutput(
        text=output.text,
        observed_bytes=output.observed_bytes,
        retained_bytes=output.retained_bytes,
        truncated=output.truncated,
        redaction_truncated=output.redaction_truncated,
        artifact=artifact,
    )


def _translate_artifact(
    plan: ValidationPlan,
    artifact: ArtifactReference,
    *,
    correlation_id: str,
) -> ValidationArtifactReference:
    assert plan.artifacts.directory is not None
    root = plan.artifacts.directory.resolve(strict=True)
    try:
        path = artifact.path.resolve(strict=True)
        relative = path.relative_to(root)
    except (OSError, ValueError) as error:
        raise ValidationEvidenceError(
            "command artifact path escaped the approved validation artifact root"
        ) from error
    rendered = relative.as_posix()
    expected_name = f"{correlation_id}.{artifact.stream.value}.log"
    try:
        stored_size = path.stat().st_size
    except OSError as error:
        raise ValidationEvidenceError("command artifact could not be inspected") from error
    if not path.is_file() or rendered != expected_name or stored_size != artifact.stored_bytes:
        raise ValidationEvidenceError(
            "command artifact identity or byte accounting did not match the validation request"
        )
    return ValidationArtifactReference(
        root_id=plan.artifacts.root_id,
        relative_path=RepositoryPath(rendered),
        stream=artifact.stream,
        status=artifact.status,
        correlation_id=correlation_id,
        observed_source_bytes=artifact.observed_bytes,
        source_bytes_retained=artifact.source_bytes_retained,
        redacted_bytes_observed=artifact.redacted_bytes_observed,
        stored_bytes=artifact.stored_bytes,
        redacted=True,
    )


def _require_output_completeness(
    command: ValidationCommand,
    stdout: ValidationCapturedOutput,
    stderr: ValidationCapturedOutput,
) -> None:
    for required, output, name in (
        (command.output.require_complete_stdout, stdout, "stdout"),
        (command.output.require_complete_stderr, stderr, "stderr"),
    ):
        if not required:
            continue
        if not (output.truncated or output.redaction_truncated):
            continue
        if output.artifact is None or output.artifact.status is not ArtifactStatus.COMPLETE:
            raise ValidationEvidenceError(
                f"required complete {name} evidence was truncated without a complete artifact"
            )


def _resolve_working_directory(
    root: Path, command: ValidationCommand
) -> tuple[Path | None, ValidationFailure | None]:
    candidate = root
    if command.relative_working_directory is not None:
        candidate = root.joinpath(*command.relative_working_directory.root.split("/"))
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None, _failure(
            ValidationFailureCategory.INVALID_EVIDENCE,
            "working_directory_outside_workspace",
            "validation working directory is unavailable or outside the approved workspace",
        )
    if not resolved.is_dir():
        return None, _failure(
            ValidationFailureCategory.INVALID_EVIDENCE,
            "working_directory_not_directory",
            "validation working directory must be an existing directory",
        )
    return resolved, None


def _prelaunch_result(
    plan: ValidationPlan,
    command: ValidationCommand,
    *,
    sequence: int,
    occurred_at: datetime,
    status: ValidationStatus,
    failure: ValidationFailure,
) -> ValidationCommandResult:
    return ValidationCommandResult(
        plan_id=plan.id,
        run_id=plan.run_id,
        work_package_id=plan.work_package_id,
        command_id=command.id,
        sequence=sequence,
        classification=command.classification,
        status=status,
        command_status=None,
        started_at=occurred_at,
        completed_at=occurred_at,
        duration_ms=0,
        executable=command.executable,
        correlation_id=validation_correlation_id(plan.id, command.id),
        expected_exit_codes=command.expected_exit_codes,
        stdout=_empty_output(),
        stderr=_empty_output(),
        failure=failure,
    )


def _invalid_result_from_command(
    plan: ValidationPlan,
    command: ValidationCommand,
    *,
    sequence: int,
    result: CommandResult,
    code: str,
    message: str,
    occurred_at: datetime | None = None,
) -> ValidationCommandResult:
    started_at = occurred_at or result.started_at
    duration_ms = (
        0 if occurred_at is not None else _duration_ms(result.started_at, result.completed_at)
    )
    return ValidationCommandResult(
        plan_id=plan.id,
        run_id=plan.run_id,
        work_package_id=plan.work_package_id,
        command_id=command.id,
        sequence=sequence,
        classification=command.classification,
        status=ValidationStatus.INVALID,
        command_status=result.status,
        started_at=started_at,
        completed_at=started_at + timedelta(milliseconds=duration_ms),
        duration_ms=duration_ms,
        executable=command.executable,
        resolved_executable=(
            result.resolved_executable
            if result.resolved_executable is not None and result.resolved_executable.is_absolute()
            else None
        ),
        correlation_id=validation_correlation_id(plan.id, command.id),
        exit_code=result.exit_code,
        expected_exit_codes=command.expected_exit_codes,
        stdout=_empty_output(),
        stderr=_empty_output(),
        failure=_failure(
            ValidationFailureCategory.INVALID_EVIDENCE,
            code,
            message,
        ),
    )


def _not_run_results(
    plan: ValidationPlan,
    *,
    start_sequence: int,
    occurred_at: datetime,
    code: str,
    message: str,
) -> list[ValidationCommandResult]:
    return [
        _prelaunch_result(
            plan,
            command,
            sequence=sequence,
            occurred_at=occurred_at,
            status=ValidationStatus.NOT_RUN,
            failure=_failure(ValidationFailureCategory.NOT_RUN, code, message),
        )
        for sequence, command in enumerate(
            plan.commands[start_sequence - 1 :], start=start_sequence
        )
    ]


def _empty_output() -> ValidationCapturedOutput:
    return ValidationCapturedOutput(
        text="",
        observed_bytes=0,
        retained_bytes=0,
        truncated=False,
    )


def _failure(
    category: ValidationFailureCategory,
    code: str,
    message: str,
    result: CommandResult | None = None,
) -> ValidationFailure:
    return ValidationFailure(
        category=category,
        code=code,
        message=message,
        command_failure_category=(
            result.failure.category if result is not None and result.failure is not None else None
        ),
    )


def _should_stop(plan: ValidationPlan, result: ValidationCommandResult) -> bool:
    if result.status is ValidationStatus.CANCELLED:
        return True
    if not plan.execution.fail_fast or result.status is ValidationStatus.PASSED:
        return False
    return not (
        result.classification is ValidationCommandClass.ADVISORY
        and result.status is ValidationStatus.FAILED
        and plan.execution.allow_advisory_failures
    )


def _aggregate_failure_status(results: tuple[ValidationCommandResult, ...]) -> ValidationStatus:
    statuses = {result.status for result in results}
    for status in (
        ValidationStatus.CANCELLED,
        ValidationStatus.TIMED_OUT,
        ValidationStatus.INVALID,
        ValidationStatus.UNAVAILABLE,
        ValidationStatus.BLOCKED,
        ValidationStatus.FAILED,
    ):
        if status in statuses:
            return status
    return ValidationStatus.INVALID


def _duration_ms(started_at: datetime, completed_at: datetime) -> int:
    delta = completed_at - started_at
    return max(0, min(86_400_000, delta // timedelta(milliseconds=1)))


def _require_utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("validation execution start must be timezone-aware UTC")
