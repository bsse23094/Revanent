"""Capability detection and controlled OpenCode builder adapter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import NoReturn

from revanent.agents.base import invalid_output_response, request_compatibility_failure
from revanent.agents.providers import (
    ProviderAdapterSettings,
    ProviderCompatibility,
    ProviderDetection,
    cancellation_before_launch,
    command_failure_response,
    deterministic_prompt,
    executable_unavailable,
    identity_for,
    invoke_command,
    prelaunch_failure_response,
    probe_text,
    provider_capabilities,
    run_probe,
    sensitive_material_failure,
    translate_success,
    validate_invocation_request,
    validate_model_id,
)
from revanent.ports.agents import (
    AgentFailure,
    AgentFailureCategory,
    AgentRequest,
    AgentResponse,
    AgentRole,
    AgentStatus,
    RetryDisposition,
    SideEffectState,
)
from revanent.ports.commands import CancellationToken, CommandRunner, CommandStatus

OPENCODE_EXECUTABLE = "opencode"
OPENCODE_SURFACE = "opencode-run-jsonl-v1"
_SUPPORTED_VERSION_PREFIXES = ("opencode 1.", "opencode version 1.")
_TOP_HELP_TOKENS = ("run",)
_RUN_HELP_TOKENS = ("--format", "json", "--model", "stdin")


def detect_opencode(runner: CommandRunner, *, working_directory: Path) -> ProviderDetection:
    """Inspect only version and help surfaces; never invoke a model."""
    version_result = run_probe(
        runner,
        executable=OPENCODE_EXECUTABLE,
        arguments=("--version",),
        working_directory=working_directory,
        correlation_id="detect-opencode-version",
    )
    if executable_unavailable(version_result):
        return _detection(
            ProviderCompatibility.UNAVAILABLE,
            version=None,
            reason="OpenCode executable was not found on the configured provider PATH",
            probes=("--version",),
        )
    version_text = probe_text(version_result).strip().splitlines()
    version = version_text[0][:128] if version_text else None
    if version_result.status is not CommandStatus.SUCCESS:
        return _detection(
            ProviderCompatibility.UNAVAILABLE,
            version=version,
            reason="OpenCode version probe did not complete successfully",
            probes=("--version",),
        )
    top = run_probe(
        runner,
        executable=OPENCODE_EXECUTABLE,
        arguments=("--help",),
        working_directory=working_directory,
        correlation_id="detect-opencode-help",
    )
    run = run_probe(
        runner,
        executable=OPENCODE_EXECUTABLE,
        arguments=("run", "--help"),
        working_directory=working_directory,
        correlation_id="detect-opencode-run-help",
    )
    compatible = (
        version is not None
        and version.casefold().startswith(_SUPPORTED_VERSION_PREFIXES)
        and top.status is CommandStatus.SUCCESS
        and run.status is CommandStatus.SUCCESS
        and _contains_exact_surface(probe_text(top), _TOP_HELP_TOKENS)
        and _contains_exact_surface(probe_text(run), _RUN_HELP_TOKENS)
    )
    if not compatible:
        return _detection(
            ProviderCompatibility.INCOMPATIBLE,
            version=version,
            reason=(
                "OpenCode is installed but its version/help does not prove the supported "
                "noninteractive JSONL stdin surface"
            ),
            probes=("--version", "--help", "run --help"),
        )
    return _detection(
        ProviderCompatibility.AVAILABLE,
        version=version,
        reason=None,
        probes=("--version", "--help", "run --help"),
    )


class OpenCodeBuilderAdapter:
    """OpenCode BUILDER adapter restricted to an approved existing worktree."""

    def __init__(
        self,
        runner: CommandRunner,
        detection: ProviderDetection,
        *,
        settings: ProviderAdapterSettings | None = None,
    ) -> None:
        if detection.provider_id != "opencode":
            raise ValueError("OpenCode adapter requires OpenCode capability detection")
        self._runner = runner
        self._detection = detection
        self._settings = settings or ProviderAdapterSettings()

    @property
    def capabilities(self):  # type: ignore[no-untyped-def]
        return self._detection.capabilities

    def invoke(
        self, request: AgentRequest, *, cancellation: CancellationToken | None = None
    ) -> AgentResponse:
        model_or_failure = _validated_model_or_failure(request)
        model = model_or_failure if isinstance(model_or_failure, str) else None
        identity = identity_for(self.capabilities, model)
        failure = model_or_failure if isinstance(model_or_failure, AgentFailure) else None
        failure = failure or request_compatibility_failure(self.capabilities, request)
        failure = failure or validate_invocation_request(request, settings=self._settings)
        failure = failure or cancellation_before_launch(request, cancellation)
        if failure is not None:
            status = (
                AgentStatus.UNAVAILABLE
                if failure.category is AgentFailureCategory.ADAPTER_UNAVAILABLE
                else AgentStatus.CANCELLED
                if failure.category is AgentFailureCategory.CANCELLATION
                else AgentStatus.FAILED
            )
            return prelaunch_failure_response(
                request, identity=identity, failure=failure, status=status
            )
        try:
            prompt = deterministic_prompt(
                request,
                mode="builder-repository-write",
                identity=identity,
            )
        except ValueError:
            return prelaunch_failure_response(
                request,
                identity=identity,
                failure=_invalid_request(
                    "provider_prompt_too_large", "provider prompt is too large"
                ),
            )
        arguments = build_opencode_arguments(model)
        sensitive_failure = sensitive_material_failure(prompt, arguments, self._settings)
        if sensitive_failure is not None:
            return prelaunch_failure_response(request, identity=identity, failure=sensitive_failure)
        result = invoke_command(
            self._runner,
            request,
            executable=OPENCODE_EXECUTABLE,
            arguments=arguments,
            prompt=prompt,
            settings=self._settings,
            cancellation=cancellation,
        )
        normalized = command_failure_response(result, request, identity=identity)
        if normalized is not None:
            return normalized
        try:
            envelope = parse_opencode_jsonl(result.stdout.text)
        except ValueError as error:
            completed = result.started_at + __import__("datetime").timedelta(
                milliseconds=int(result.duration_seconds * 1_000)
            )
            return invalid_output_response(
                request,
                identity=identity,
                started_at=result.started_at,
                completed_at=completed,
                category=AgentFailureCategory.MALFORMED_OUTPUT,
                code=str(error),
                message="OpenCode JSONL output did not satisfy the supported event contract",
            )
        return translate_success(
            result,
            request,
            identity=identity,
            envelope=envelope,
            sensitive_values=self._settings.sensitive_values,
        )


def build_opencode_arguments(model: str | None) -> tuple[str, ...]:
    """Build the frozen noninteractive OpenCode v1 surface; no arbitrary flags."""
    arguments = ["run", "--format", "json"]
    if model is not None:
        arguments.extend(("--model", validate_model_id(model) or ""))
    arguments.extend(("--", "-"))
    return tuple(arguments)


def parse_opencode_jsonl(text: str) -> bytes:
    """Extract exactly one AgentResponse document from a coherent OpenCode JSONL run."""
    if "\ufffd" in text:
        raise ValueError("invalid_utf8")
    events = _parse_json_lines(text)
    terminal_text: str | None = None
    finished = False
    for event in events:
        event_type = event.get("type")
        if event_type == "step_start":
            if finished:
                raise ValueError("contradictory_terminal_events")
        elif event_type == "text":
            part = event.get("part")
            if (
                terminal_text is not None
                or not isinstance(part, dict)
                or set(part) != {"text"}
                or not isinstance(part.get("text"), str)
            ):
                raise ValueError("malformed_provider_event")
            terminal_text = part["text"]
        elif event_type == "step_finish":
            if finished or terminal_text is None:
                raise ValueError("missing_terminal_event")
            finished = True
        else:
            raise ValueError("unknown_provider_event")
    if not finished or terminal_text is None:
        raise ValueError("missing_terminal_event")
    return terminal_text.encode("utf-8")


def _parse_json_lines(text: str) -> list[dict[str, object]]:
    if len(text.encode("utf-8")) > 1 * 1_024 * 1_024:
        raise ValueError("provider_output_too_large")
    lines = text.splitlines()
    if not lines or any(not line.strip() for line in lines):
        raise ValueError("malformed_jsonl")
    events: list[dict[str, object]] = []
    for line in lines:
        try:
            value = json.loads(
                line,
                object_pairs_hook=_reject_duplicates,
                parse_constant=_reject_constant,
            )
        except (ValueError, json.JSONDecodeError, RecursionError) as error:
            raise ValueError("malformed_jsonl") from error
        if not isinstance(value, dict):
            raise ValueError("malformed_provider_event")
        events.append(value)
    return events


def _reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate_json_key")
        value[key] = item
    return value


def _reject_constant(value: str) -> NoReturn:
    raise ValueError(value)


def _detection(
    compatibility: ProviderCompatibility,
    *,
    version: str | None,
    reason: str | None,
    probes: tuple[str, ...],
) -> ProviderDetection:
    return ProviderDetection(
        provider_id="opencode",
        compatibility=compatibility,
        version=version,
        reason=reason,
        capabilities=provider_capabilities(
            provider="opencode",
            adapter="opencode.builder",
            roles=(AgentRole.BUILDER,),
            read_only=False,
            writes=True,
            repair=False,
            compatibility=compatibility,
            version=version,
            reason=reason,
            surface=OPENCODE_SURFACE,
        ),
        probes=probes,
    )


def _contains_exact_surface(help_text: str, tokens: tuple[str, ...]) -> bool:
    folded = help_text.casefold()
    return all(token.casefold() in folded for token in tokens)


def _validated_model_or_failure(request: AgentRequest) -> str | AgentFailure | None:
    try:
        return validate_model_id(request.routing.model)
    except ValueError:
        return _invalid_request("invalid_model_identifier", "provider model identifier is invalid")


def _invalid_request(code: str, message: str) -> AgentFailure:
    return AgentFailure(
        category=AgentFailureCategory.INVALID_REQUEST,
        code=code,
        message=message,
        retry=RetryDisposition.NOT_RETRYABLE,
        side_effects=SideEffectState.NONE,
    )
