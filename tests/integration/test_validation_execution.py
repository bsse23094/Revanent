from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path

from revanent.agents import (
    FakeAgentAdapter,
    FakeAgentScenario,
    FakeAgentStep,
    ScriptedResponseOutcome,
    agent_request_digest,
)
from revanent.commands import (
    CommandPolicy,
    EnvironmentPolicy,
    ExecutablePolicy,
    ExecutableRule,
    LocalCommandRunner,
    PathPolicy,
    Redactor,
)
from revanent.ports import (
    AgentRequest,
    AgentRole,
    AgentStatus,
    ArtifactStatus,
    EnvironmentOverrides,
    RepositoryPath,
    ReviewerPayload,
    ScenarioId,
    StructuredParseStatus,
    ValidationCommand,
    ValidationCommandId,
    ValidationOutputPolicy,
    ValidationStatus,
    WorkspaceReference,
)
from revanent.review import ReviewGate, ReviewGateInput, ReviewGateStatus
from revanent.validation import ValidationRunner
from tests.agent_factories import NOW, make_capabilities, make_request, make_response
from tests.validation_factories import (
    REVIEW_INVOCATION_ID,
    RUN_ID,
    WORK_PACKAGE_ID,
    make_local_approval_evidence,
    make_validation_plan,
)

FAKE_COMMAND = Path(__file__).parents[1] / "fixtures" / "fake_command.py"


def _baseline_environment() -> dict[str, str]:
    names = ("SYSTEMROOT", "WINDIR") if os.name == "nt" else ()
    return {name: os.environ[name] for name in names if name in os.environ}


def _runner(
    worktree: Path,
    *,
    artifact_root: Path | None = None,
    secrets: tuple[str, ...] = (),
) -> LocalCommandRunner:
    executable = Path(sys.executable).resolve(strict=True)
    extensions = (executable.suffix,) if os.name == "nt" else ()
    return LocalCommandRunner(
        executable_policy=ExecutablePolicy(
            (
                ExecutableRule(
                    "fixture-python",
                    (executable,),
                    allowed_extensions=extensions,
                ),
            )
        ),
        path_policy=PathPolicy(
            (worktree.resolve(strict=True),),
            artifact_roots=(artifact_root.resolve(strict=True),) if artifact_root else (),
        ),
        environment_policy=EnvironmentPolicy(
            _baseline_environment(),
            allowed_override_keys=frozenset({"API_TOKEN"}),
            allowed_sensitive_keys=frozenset({"API_TOKEN"}),
        ),
        command_policy=CommandPolicy(
            max_timeout_seconds=5,
            max_stdout_bytes=1 * 1_024 * 1_024,
            max_stderr_bytes=1 * 1_024 * 1_024,
            max_artifact_bytes_per_stream=8 * 1_024 * 1_024,
            allow_artifacts=True,
        ),
        redactor=Redactor(secrets),
        poll_interval_seconds=0.005,
        termination_grace_seconds=0.5,
    )


def _command(
    command_id: str,
    name: str,
    mode: str,
    *arguments: str,
    output: ValidationOutputPolicy | None = None,
    relative_directory: str | None = None,
    environment_names: tuple[str, ...] = (),
    timeout_seconds: int = 2,
) -> ValidationCommand:
    return ValidationCommand(
        id=ValidationCommandId(command_id),
        name=name,
        executable="fixture-python",
        arguments=(str(FAKE_COMMAND), mode, *arguments),
        relative_working_directory=(
            RepositoryPath(relative_directory) if relative_directory else None
        ),
        timeout_seconds=timeout_seconds,
        output=output or ValidationOutputPolicy(),
        allowed_environment_names=environment_names,
    )


def test_real_controlled_commands_execute_in_order_and_preserve_streams(
    tmp_path: Path,
) -> None:
    worktree = tmp_path / "owned worktree"
    subdirectory = worktree / "package"
    worktree.mkdir()
    subdirectory.mkdir()
    commands = (
        _command("vcmd_cwd", "working directory", "cwd", relative_directory="package"),
        _command("vcmd_streams", "separate streams", "streams", "stdout", "stderr"),
    )
    plan = make_validation_plan(worktree, commands=commands)

    result = ValidationRunner(_runner(worktree)).execute(plan, started_at=NOW)

    assert result.status is ValidationStatus.PASSED
    assert Path(result.commands[0].stdout.text.strip()) == subdirectory.resolve()
    assert result.commands[1].stdout.text == "stdout"
    assert result.commands[1].stderr.text == "stderr"
    assert [item.sequence for item in result.commands] == [1, 2]


def test_real_nonzero_exit_remains_failed_despite_success_prose(tmp_path: Path) -> None:
    worktree = tmp_path / "owned-worktree"
    worktree.mkdir()
    command = _command("vcmd_exit", "failing command", "streams", "passed", "failure")
    data = command.model_dump()
    data["expected_exit_codes"] = (7,)
    expected_seven = ValidationCommand.model_validate(data)
    # The fixture succeeds with zero, but the declared contract expects seven.
    plan = make_validation_plan(worktree, commands=(expected_seven,))

    result = ValidationRunner(_runner(worktree)).execute(plan, started_at=NOW)

    assert result.status is ValidationStatus.FAILED
    assert result.commands[0].stdout.text == "passed"
    assert result.commands[0].exit_code == 0


def test_real_sensitive_environment_echo_is_redacted_from_evidence(tmp_path: Path) -> None:
    worktree = tmp_path / "owned-worktree"
    worktree.mkdir()
    secret = "fixture-validation-secret"
    command = _command(
        "vcmd_environment",
        "selected environment",
        "selected-env",
        "API_TOKEN",
        environment_names=("API_TOKEN",),
    )
    plan = make_validation_plan(worktree, commands=(command,))

    result = ValidationRunner(_runner(worktree, secrets=(secret,))).execute(
        plan,
        started_at=NOW,
        environment=EnvironmentOverrides.from_mapping({"API_TOKEN": secret}),
    )

    assert result.status is ValidationStatus.PASSED
    assert secret not in result.model_dump_json()
    assert "[REDACTED]" in result.commands[0].stdout.text
    assert secret not in plan.model_dump_json()


def test_real_bounded_overflow_artifact_is_relative_and_correlated(tmp_path: Path) -> None:
    worktree = tmp_path / "owned-worktree"
    artifacts = tmp_path / "validation-artifacts"
    worktree.mkdir()
    artifacts.mkdir()
    output = ValidationOutputPolicy(
        stdout_bytes=64,
        stderr_bytes=64,
        artifact_bytes_per_stream=4_096,
        capture_artifacts=True,
        require_complete_stdout=True,
        require_complete_stderr=True,
    )
    command = _command("vcmd_flood", "bounded output", "flood", "1024", output=output)
    plan = make_validation_plan(
        worktree,
        commands=(command,),
        artifact_directory=artifacts,
    )

    result = ValidationRunner(_runner(worktree, artifact_root=artifacts)).execute(
        plan, started_at=NOW
    )

    assert result.status is ValidationStatus.PASSED
    for captured in (result.commands[0].stdout, result.commands[0].stderr):
        assert captured.truncated
        assert captured.artifact is not None
        assert captured.artifact.status is ArtifactStatus.COMPLETE
        assert captured.artifact.root_id == "validation-artifacts.fixture"
        assert captured.artifact.correlation_id == result.commands[0].correlation_id
        assert not Path(captured.artifact.relative_path.root).is_absolute()


def test_real_timeout_is_typed_and_never_approvable(tmp_path: Path) -> None:
    worktree = tmp_path / "owned-worktree"
    worktree.mkdir()
    launched = worktree / "launched.txt"
    release = worktree / "release.txt"
    command = _command(
        "vcmd_timeout",
        "bounded timeout",
        "block",
        str(launched),
        str(release),
        timeout_seconds=1,
    )
    plan = make_validation_plan(worktree, commands=(command,))

    result = ValidationRunner(_runner(worktree)).execute(plan, started_at=NOW)

    assert launched.exists()
    assert result.status is ValidationStatus.TIMED_OUT
    assert result.commands[0].status is ValidationStatus.TIMED_OUT
    assert not result.approvable


def test_real_validation_plus_fake_reviewer_creates_only_local_approval(
    tmp_path: Path,
) -> None:
    worktree = tmp_path / "owned-worktree"
    worktree.mkdir()
    command = _command("vcmd_pass", "required validation", "exit", "0")
    plan = make_validation_plan(worktree, commands=(command,))
    validation = ValidationRunner(_runner(worktree)).execute(plan, started_at=NOW)

    base_request = make_request(AgentRole.REVIEWER)
    request = AgentRequest.model_validate(
        {
            **base_request.model_dump(),
            "work_package_id": WORK_PACKAGE_ID,
            "workspace": WorkspaceReference(
                kind=base_request.workspace.kind,
                reference_id="worktree.validation",
                root=worktree.resolve(strict=True),
            ),
        }
    )
    base_response = make_response(AgentRole.REVIEWER)
    assert isinstance(base_response.payload, ReviewerPayload)
    scenario = FakeAgentScenario(
        scenario_id=ScenarioId("review-gate-fixture"),
        capabilities=make_capabilities(),
        default_timestamp=validation.completed_at,
        steps=(
            FakeAgentStep(
                expected_request_sha256=agent_request_digest(request),
                started_at=validation.completed_at + timedelta(seconds=1),
                duration_ms=1_000,
                outcome=ScriptedResponseOutcome(
                    status=AgentStatus.COMPLETED,
                    summary="Fake structured review completed",
                    structured_parse_status=StructuredParseStatus.PARSED,
                    payload=base_response.payload,
                ),
            ),
        ),
    )

    reviewer_response = FakeAgentAdapter(scenario).invoke(request)
    decision = ReviewGate().evaluate(
        ReviewGateInput(
            expected_run_id=RUN_ID,
            expected_work_package_id=WORK_PACKAGE_ID,
            expected_review_invocation_id=REVIEW_INVOCATION_ID,
            validation_plan=plan,
            validation_result=validation,
            reviewer_response=reviewer_response,
            local_evidence=make_local_approval_evidence(
                observed_at=reviewer_response.completed_at + timedelta(seconds=1)
            ),
            evaluated_at=validation.completed_at + timedelta(seconds=4),
        )
    )

    assert reviewer_response.status is AgentStatus.COMPLETED
    assert decision.status is ReviewGateStatus.APPROVABLE
    assert decision.approval_gate is not None
    assert decision.approval_gate.is_satisfied
