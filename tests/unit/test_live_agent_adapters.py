from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from revanent.agents import (
    CodexRepairAdapter,
    CodexReviewerAdapter,
    OpenCodeBuilderAdapter,
    ProviderAdapterSettings,
    ProviderCompatibility,
    build_codex_arguments,
    build_opencode_arguments,
    detect_codex,
    detect_opencode,
    parse_codex_jsonl,
    parse_opencode_jsonl,
)
from revanent.ports import (
    AdapterId,
    AgentFailureCategory,
    AgentProviderIdentity,
    AgentRequest,
    AgentResponse,
    AgentRole,
    AgentRouting,
    AgentStatus,
    CapturedOutput,
    CommandFailure,
    CommandFailureCategory,
    CommandRequest,
    CommandResult,
    CommandStatus,
    EnvironmentOverrides,
    EnvironmentVariable,
    ProviderId,
    RetryDisposition,
    SideEffectState,
    StructuredParseStatus,
    WorkspaceReference,
)
from tests.agent_factories import NOW, make_request, make_response


@dataclass
class ProviderRunner:
    provider: str
    status: CommandStatus = CommandStatus.SUCCESS
    help_compatible: bool = True
    response_role: AgentRole | None = None
    response_status: AgentStatus = AgentStatus.COMPLETED
    requests: list[CommandRequest] = field(default_factory=list)

    def run(self, request: CommandRequest) -> CommandResult:
        self.requests.append(request)
        output = self._output(request)
        failure = None
        exit_code: int | None = 0
        if self.status not in {CommandStatus.SUCCESS, CommandStatus.NONZERO_EXIT}:
            failure = CommandFailure(
                category=(
                    CommandFailureCategory.EXECUTABLE_UNAVAILABLE
                    if self.status is CommandStatus.POLICY_REJECTED
                    else CommandFailureCategory.LAUNCH
                ),
                message="sanitized fixture failure",
            )
            exit_code = None
        elif self.status is CommandStatus.NONZERO_EXIT:
            exit_code = 7
        return CommandResult(
            correlation_id=request.correlation_id,
            executable=request.executable,
            resolved_executable=None,
            status=self.status,
            started_at=NOW,
            completed_at=NOW + timedelta(seconds=1),
            duration_seconds=1.0,
            stdout=CapturedOutput(
                text=output,
                observed_bytes=len(output.encode()),
                retained_bytes=len(output.encode()),
                truncated=False,
            ),
            stderr=CapturedOutput(text="", observed_bytes=0, retained_bytes=0, truncated=False),
            exit_code=exit_code,
            failure=failure,
        )

    def _output(self, request: CommandRequest) -> str:
        if request.arguments == ("--version",):
            return (
                "codex-cli 0.146.0-alpha.3.1\n" if self.provider == "codex" else "opencode 1.2.3\n"
            )
        if request.arguments in {("--help",), ("exec", "--help"), ("run", "--help")}:
            if not self.help_compatible:
                return "Usage: unsupported\n"
            if self.provider == "codex":
                return (
                    "exec --ask-for-approval\n"
                    if request.arguments == ("--help",)
                    else "--json --sandbox --ephemeral --ignore-user-config stdin "
                    "read-only workspace-write\n"
                )
            return (
                "run\n" if request.arguments == ("--help",) else "run --format json --model stdin\n"
            )
        role = self.response_role or (
            AgentRole.BUILDER
            if self.provider == "opencode"
            else AgentRole.REPAIRER
            if "workspace-write" in request.arguments
            else AgentRole.REVIEWER
        )
        response = make_response(role).model_copy(
            update={
                "identity": AgentProviderIdentity(
                    provider_id=ProviderId(self.provider),
                    adapter_id=AdapterId(
                        "opencode.builder"
                        if self.provider == "opencode"
                        else "codex.repairer"
                        if role is AgentRole.REPAIRER
                        else "codex.reviewer"
                    ),
                    adapter_version="1.0.0",
                    model="fixture-model",
                )
            }
        )
        if self.response_status is not AgentStatus.COMPLETED:
            category = (
                AgentFailureCategory.EXTERNAL_BLOCKER
                if self.response_status is AgentStatus.BLOCKED
                else AgentFailureCategory.PROVIDER_FAILURE
            )
            response = AgentResponse.model_validate(
                {
                    **response.model_dump(),
                    "status": self.response_status,
                    "payload": None,
                    "structured_parse_status": StructuredParseStatus.NOT_PROVIDED,
                    "failure": {
                        "category": category,
                        "code": "fake_provider_outcome",
                        "message": "Fake provider reported a terminal outcome",
                        "retry": RetryDisposition.UNKNOWN,
                        "side_effects": SideEffectState.POSSIBLE,
                    },
                }
            )
        envelope = response.model_dump_json()
        if self.provider == "opencode":
            return "\n".join(
                (
                    json.dumps({"type": "step_start"}),
                    json.dumps({"type": "text", "part": {"text": envelope}}),
                    json.dumps({"type": "step_finish"}),
                )
            )
        return "\n".join(
            (
                json.dumps({"type": "thread.started"}),
                json.dumps({"type": "turn.started"}),
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"type": "agent_message", "text": envelope},
                    }
                ),
                json.dumps({"type": "turn.completed"}),
            )
        )


def _request(role: AgentRole, root: Path) -> AgentRequest:
    base = make_request(role)
    provider = "opencode" if role is AgentRole.BUILDER else "codex"
    return base.model_copy(
        update={
            "workspace": WorkspaceReference(
                kind=base.workspace.kind,
                reference_id="owned.fixture",
                root=root.resolve(strict=True),
            ),
            "routing": AgentRouting(provider_id=ProviderId(provider), model="fixture-model"),
        }
    )


def test_compatible_surfaces_map_to_distinct_capabilities(tmp_path: Path) -> None:
    open_runner = ProviderRunner("opencode")
    codex_runner = ProviderRunner("codex")

    opencode = detect_opencode(open_runner, working_directory=tmp_path)
    codex = detect_codex(codex_runner, working_directory=tmp_path)

    assert opencode.compatibility is ProviderCompatibility.AVAILABLE
    assert opencode.capabilities.supported_roles == (AgentRole.BUILDER,)
    assert codex.compatibility is ProviderCompatibility.AVAILABLE
    assert codex.reviewer_capabilities.supports_read_only
    assert not codex.reviewer_capabilities.supports_repository_writes
    assert codex.repair_capabilities.supports_repair
    assert codex.repair_surface_verified
    assert [request.arguments for request in codex_runner.requests] == [
        ("--version",),
        ("--help",),
        ("exec", "--help"),
    ]


@pytest.mark.parametrize("provider", ["opencode", "codex"])
def test_missing_provider_is_actionable_unavailable(provider: str, tmp_path: Path) -> None:
    runner = ProviderRunner(provider, status=CommandStatus.POLICY_REJECTED)
    detection = (
        detect_opencode(runner, working_directory=tmp_path)
        if provider == "opencode"
        else detect_codex(runner, working_directory=tmp_path)
    )
    assert detection.compatibility is ProviderCompatibility.UNAVAILABLE
    assert detection.reason is not None
    assert len(runner.requests) == 1


@pytest.mark.parametrize("provider", ["opencode", "codex"])
def test_incompatible_help_fails_closed(provider: str, tmp_path: Path) -> None:
    runner = ProviderRunner(provider, help_compatible=False)
    detection = (
        detect_opencode(runner, working_directory=tmp_path)
        if provider == "opencode"
        else detect_codex(runner, working_directory=tmp_path)
    )
    assert detection.compatibility is ProviderCompatibility.INCOMPATIBLE


def test_argument_builders_are_exact_distinct_and_reject_option_models() -> None:
    assert build_opencode_arguments("fixture-model") == (
        "run",
        "--format",
        "json",
        "--model",
        "fixture-model",
        "--",
        "-",
    )
    assert build_codex_arguments(sandbox="read-only", model="fixture-model") == (
        "--ask-for-approval",
        "never",
        "exec",
        "--ignore-user-config",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--json",
        "--color",
        "never",
        "--model",
        "fixture-model",
        "-",
    )
    assert "workspace-write" in build_codex_arguments(
        sandbox="workspace-write", model="fixture-model"
    )
    with pytest.raises(ValueError, match="model identifier"):
        build_codex_arguments(sandbox="read-only", model="--dangerously-bypass")


@pytest.mark.parametrize(
    ("role", "expected_sandbox"),
    [(AgentRole.REVIEWER, "read-only"), (AgentRole.REPAIRER, "workspace-write")],
)
def test_codex_adapters_launch_only_their_exact_mode(
    role: AgentRole, expected_sandbox: str, tmp_path: Path
) -> None:
    runner = ProviderRunner("codex")
    detection = detect_codex(runner, working_directory=tmp_path)
    runner.requests.clear()
    adapter = (
        CodexReviewerAdapter(runner, detection)
        if role is AgentRole.REVIEWER
        else CodexRepairAdapter(runner, detection, write_authorized=True)
    )

    response = adapter.invoke(_request(role, tmp_path))

    assert response.status is AgentStatus.COMPLETED
    assert response.role is role
    assert len(runner.requests) == 1
    command = runner.requests[0]
    assert command.working_directory == tmp_path.resolve()
    assert command.arguments == build_codex_arguments(
        sandbox=expected_sandbox, model="fixture-model"
    )
    assert command.stdin is not None


def test_builder_launches_exact_surface_and_preserves_claims(tmp_path: Path) -> None:
    runner = ProviderRunner("opencode")
    detection = detect_opencode(runner, working_directory=tmp_path)
    runner.requests.clear()

    response = OpenCodeBuilderAdapter(runner, detection).invoke(
        _request(AgentRole.BUILDER, tmp_path)
    )

    assert response.status is AgentStatus.COMPLETED
    assert response.payload is not None
    assert response.payload.role is AgentRole.BUILDER
    assert runner.requests[0].arguments == build_opencode_arguments("fixture-model")


@pytest.mark.parametrize(
    ("provider", "role", "provider_status"),
    [
        ("opencode", AgentRole.BUILDER, AgentStatus.BLOCKED),
        ("codex", AgentRole.REVIEWER, AgentStatus.FAILED),
    ],
)
def test_provider_reported_terminal_outcomes_remain_typed_claims(
    provider: str,
    role: AgentRole,
    provider_status: AgentStatus,
    tmp_path: Path,
) -> None:
    runner = ProviderRunner(provider, response_status=provider_status)
    if provider == "opencode":
        detection = detect_opencode(runner, working_directory=tmp_path)
        runner.requests.clear()
        response = OpenCodeBuilderAdapter(runner, detection).invoke(_request(role, tmp_path))
    else:
        codex_detection = detect_codex(runner, working_directory=tmp_path)
        runner.requests.clear()
        response = CodexReviewerAdapter(runner, codex_detection).invoke(_request(role, tmp_path))

    assert response.status is provider_status
    assert response.failure is not None
    assert response.payload is None


def test_role_mismatch_and_repair_without_authority_fail_before_launch(tmp_path: Path) -> None:
    runner = ProviderRunner("codex")
    detection = detect_codex(runner, working_directory=tmp_path)
    runner.requests.clear()

    wrong = CodexReviewerAdapter(runner, detection).invoke(_request(AgentRole.REPAIRER, tmp_path))
    unauthorized = CodexRepairAdapter(runner, detection, write_authorized=False).invoke(
        _request(AgentRole.REPAIRER, tmp_path)
    )

    assert wrong.status is AgentStatus.FAILED
    assert wrong.failure is not None
    assert wrong.failure.category is AgentFailureCategory.UNSUPPORTED_CAPABILITY
    assert unauthorized.status is AgentStatus.FAILED
    assert unauthorized.failure is not None
    assert unauthorized.failure.code == "repair_not_authorized"
    assert runner.requests == []


def test_unknown_environment_key_fails_before_launch(tmp_path: Path) -> None:
    runner = ProviderRunner("opencode")
    detection = detect_opencode(runner, working_directory=tmp_path)
    runner.requests.clear()
    settings = ProviderAdapterSettings(
        environment=EnvironmentOverrides((EnvironmentVariable("NOT_ALLOWED", "value"),))
    )

    response = OpenCodeBuilderAdapter(runner, detection, settings=settings).invoke(
        _request(AgentRole.BUILDER, tmp_path)
    )

    assert response.status is AgentStatus.FAILED
    assert response.failure is not None
    assert response.failure.code == "environment_not_authorized"
    assert runner.requests == []


@pytest.mark.parametrize(
    ("status", "agent_status", "category"),
    [
        (CommandStatus.NONZERO_EXIT, AgentStatus.FAILED, AgentFailureCategory.PROVIDER_FAILURE),
        (CommandStatus.TIMEOUT, AgentStatus.TIMED_OUT, AgentFailureCategory.TIMEOUT),
        (CommandStatus.CANCELLED, AgentStatus.CANCELLED, AgentFailureCategory.CANCELLATION),
        (
            CommandStatus.LAUNCH_FAILED,
            AgentStatus.FAILED,
            AgentFailureCategory.INVOCATION_FAILURE,
        ),
        (
            CommandStatus.POLICY_REJECTED,
            AgentStatus.UNAVAILABLE,
            AgentFailureCategory.ADAPTER_UNAVAILABLE,
        ),
        (
            CommandStatus.OUTPUT_ARTIFACT_FAILED,
            AgentStatus.FAILED,
            AgentFailureCategory.ARTIFACT_FAILURE,
        ),
    ],
)
def test_command_outcomes_are_normalized(
    status: CommandStatus,
    agent_status: AgentStatus,
    category: AgentFailureCategory,
    tmp_path: Path,
) -> None:
    detection_runner = ProviderRunner("opencode")
    detection = detect_opencode(detection_runner, working_directory=tmp_path)
    runner = ProviderRunner("opencode", status=status)

    response = OpenCodeBuilderAdapter(runner, detection).invoke(
        _request(AgentRole.BUILDER, tmp_path)
    )

    assert response.status is agent_status
    assert response.failure is not None
    assert response.failure.category is category
    if status is not CommandStatus.POLICY_REJECTED:
        assert response.failure.side_effects.value == "POSSIBLE"


@pytest.mark.parametrize(
    "text",
    [
        "",
        '{"type":"turn.started","type":"turn.completed"}',
        '{"type":"unknown"}',
        '{"type":"turn.started"}',
        '{"type":"turn.started"}\n{"type":"turn.completed"}',
        "{not-json}",
    ],
)
def test_codex_jsonl_rejects_malformed_or_incoherent_streams(text: str) -> None:
    with pytest.raises(ValueError):
        parse_codex_jsonl(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        '{"type":"text","type":"step_finish"}',
        '{"type":"unknown"}',
        '{"type":"step_start"}',
        "{not-json}",
    ],
)
def test_opencode_jsonl_rejects_malformed_or_incoherent_streams(text: str) -> None:
    with pytest.raises(ValueError):
        parse_opencode_jsonl(text)


def test_prompt_is_bounded_and_contains_no_environment_values(tmp_path: Path) -> None:
    runner = ProviderRunner("codex")
    detection = detect_codex(runner, working_directory=tmp_path)
    runner.requests.clear()
    request = _request(AgentRole.REVIEWER, tmp_path)
    request = request.model_copy(update={"allowed_environment_names": ("API_TOKEN",)})
    settings = ProviderAdapterSettings(
        environment=EnvironmentOverrides((EnvironmentVariable("API_TOKEN", "secret-value"),)),
        sensitive_values=("secret-value",),
    )

    response = CodexReviewerAdapter(runner, detection, settings=settings).invoke(request)

    assert response.status is AgentStatus.COMPLETED
    command = runner.requests[0]
    assert command.stdin is not None
    assert b"secret-value" not in command.stdin
    assert "secret-value" not in repr(request)


def test_prelaunch_timestamp_is_utc_and_does_not_depend_on_provider(tmp_path: Path) -> None:
    runner = ProviderRunner("codex")
    detection = detect_codex(runner, working_directory=tmp_path)
    runner.requests.clear()
    response = CodexRepairAdapter(runner, detection, write_authorized=False).invoke(
        _request(AgentRole.REPAIRER, tmp_path)
    )
    assert response.started_at.utcoffset() == UTC.utcoffset(datetime.now(UTC))
