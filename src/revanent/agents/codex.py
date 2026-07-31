"""Capability detection and separated Codex review/repair adapters."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import NoReturn

from revanent.agents.base import invalid_output_response, request_compatibility_failure
from revanent.agents.providers import (
    ProviderAdapterSettings,
    ProviderCompatibility,
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
    AgentCapabilities,
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

CODEX_EXECUTABLE = "codex"
CODEX_REVIEW_SURFACE = "codex-exec-jsonl-read-only-v1"
CODEX_REPAIR_SURFACE = "codex-exec-jsonl-workspace-write-v1"
_SUPPORTED_VERSION_PREFIXES = ("codex-cli 0.146.",)
_TOP_HELP_TOKENS = ("exec", "--ask-for-approval")
_EXEC_COMMON_TOKENS = (
    "--json",
    "--sandbox",
    "--ephemeral",
    "--ignore-user-config",
    "read-only",
)


@dataclass(frozen=True, slots=True)
class CodexDetection:
    """Sanitized Codex surface result with separately routed capabilities."""

    compatibility: ProviderCompatibility
    version: str | None
    reason: str | None
    reviewer_capabilities: AgentCapabilities
    repair_capabilities: AgentCapabilities
    repair_surface_verified: bool
    probes: tuple[str, ...]


def detect_codex(runner: CommandRunner, *, working_directory: Path) -> CodexDetection:
    """Inspect version/top-level/exec help only; never submit a prompt."""
    version_result = run_probe(
        runner,
        executable=CODEX_EXECUTABLE,
        arguments=("--version",),
        working_directory=working_directory,
        correlation_id="detect-codex-version",
    )
    if executable_unavailable(version_result):
        return _detection(
            ProviderCompatibility.UNAVAILABLE,
            version=None,
            reason="Codex executable was not found on the configured provider PATH",
            repair=False,
            probes=("--version",),
        )
    lines = probe_text(version_result).strip().splitlines()
    version = lines[0][:128] if lines else None
    if version_result.status is not CommandStatus.SUCCESS:
        return _detection(
            ProviderCompatibility.UNAVAILABLE,
            version=version,
            reason="Codex version probe did not complete successfully",
            repair=False,
            probes=("--version",),
        )
    top = run_probe(
        runner,
        executable=CODEX_EXECUTABLE,
        arguments=("--help",),
        working_directory=working_directory,
        correlation_id="detect-codex-help",
    )
    execute = run_probe(
        runner,
        executable=CODEX_EXECUTABLE,
        arguments=("exec", "--help"),
        working_directory=working_directory,
        correlation_id="detect-codex-exec-help",
    )
    top_text = probe_text(top)
    exec_text = probe_text(execute)
    common = (
        version is not None
        and version.casefold().startswith(_SUPPORTED_VERSION_PREFIXES)
        and top.status is CommandStatus.SUCCESS
        and execute.status is CommandStatus.SUCCESS
        and _contains(top_text, _TOP_HELP_TOKENS)
        and _contains(exec_text, _EXEC_COMMON_TOKENS)
        and "stdin" in exec_text.casefold()
    )
    repair = common and "workspace-write" in exec_text.casefold()
    if not common:
        return _detection(
            ProviderCompatibility.INCOMPATIBLE,
            version=version,
            reason=(
                "Codex is installed but its version/help does not prove the supported "
                "noninteractive JSONL read-only surface"
            ),
            repair=False,
            probes=("--version", "--help", "exec --help"),
        )
    return _detection(
        ProviderCompatibility.AVAILABLE,
        version=version,
        reason=None,
        repair=repair,
        probes=("--version", "--help", "exec --help"),
    )


class CodexReviewerAdapter:
    """Codex REVIEWER adapter with a frozen read-only sandbox invocation."""

    def __init__(
        self,
        runner: CommandRunner,
        detection: CodexDetection,
        *,
        settings: ProviderAdapterSettings | None = None,
    ) -> None:
        self._runner = runner
        self._detection = detection
        self._settings = settings or ProviderAdapterSettings()

    @property
    def capabilities(self) -> AgentCapabilities:
        return self._detection.reviewer_capabilities

    def invoke(
        self, request: AgentRequest, *, cancellation: CancellationToken | None = None
    ) -> AgentResponse:
        return _invoke_codex(
            runner=self._runner,
            capabilities=self.capabilities,
            settings=self._settings,
            request=request,
            mode="reviewer-read-only",
            sandbox="read-only",
            cancellation=cancellation,
        )


class CodexRepairAdapter:
    """Codex REPAIRER adapter requiring explicit constructor and request authority."""

    def __init__(
        self,
        runner: CommandRunner,
        detection: CodexDetection,
        *,
        write_authorized: bool,
        settings: ProviderAdapterSettings | None = None,
    ) -> None:
        if type(write_authorized) is not bool:
            raise TypeError("write_authorized must be an explicit boolean")
        self._runner = runner
        self._detection = detection
        self._write_authorized = write_authorized
        self._settings = settings or ProviderAdapterSettings()

    @property
    def capabilities(self) -> AgentCapabilities:
        return self._detection.repair_capabilities

    def invoke(
        self, request: AgentRequest, *, cancellation: CancellationToken | None = None
    ) -> AgentResponse:
        if not self._write_authorized:
            identity = identity_for(self.capabilities, _safe_model(request))
            return prelaunch_failure_response(
                request,
                identity=identity,
                failure=_invalid_request(
                    "repair_not_authorized",
                    "Codex repair requires explicit write authorization",
                ),
            )
        return _invoke_codex(
            runner=self._runner,
            capabilities=self.capabilities,
            settings=self._settings,
            request=request,
            mode="repairer-explicit-repository-write",
            sandbox="workspace-write",
            cancellation=cancellation,
        )


def build_codex_arguments(*, sandbox: str, model: str | None) -> tuple[str, ...]:
    """Build one of two frozen exec surfaces; caller cannot provide arbitrary flags."""
    if sandbox not in {"read-only", "workspace-write"}:
        raise ValueError("unsupported Codex sandbox mode")
    arguments = [
        "--ask-for-approval",
        "never",
        "exec",
        "--ignore-user-config",
        "--ephemeral",
        "--sandbox",
        sandbox,
        "--json",
        "--color",
        "never",
    ]
    if model is not None:
        arguments.extend(("--model", validate_model_id(model) or ""))
    arguments.append("-")
    return tuple(arguments)


def parse_codex_jsonl(text: str) -> bytes:
    """Extract one terminal AgentResponse from the verified Codex JSONL event grammar."""
    if "\ufffd" in text:
        raise ValueError("invalid_utf8")
    events = _parse_json_lines(text)
    terminal_text: str | None = None
    turn_started = False
    turn_completed = False
    allowed_items = {"reasoning", "command_execution", "file_change"}
    for event in events:
        event_type = event.get("type")
        if event_type == "thread.started":
            if turn_started or turn_completed:
                raise ValueError("contradictory_terminal_events")
        elif event_type == "turn.started":
            if turn_started or turn_completed:
                raise ValueError("contradictory_terminal_events")
            turn_started = True
        elif event_type in {"item.started", "item.completed"}:
            if not turn_started or turn_completed:
                raise ValueError("malformed_provider_event")
            item = event.get("item")
            if not isinstance(item, dict) or not isinstance(item.get("type"), str):
                raise ValueError("malformed_provider_event")
            item_type = item["type"]
            if item_type == "agent_message" and event_type == "item.completed":
                text_value = item.get("text")
                if terminal_text is not None or not isinstance(text_value, str):
                    raise ValueError("contradictory_terminal_events")
                terminal_text = text_value
            elif item_type not in allowed_items:
                raise ValueError("unknown_provider_event")
        elif event_type == "turn.completed":
            if not turn_started or turn_completed or terminal_text is None:
                raise ValueError("missing_terminal_event")
            turn_completed = True
        else:
            raise ValueError("unknown_provider_event")
    if not turn_completed or terminal_text is None:
        raise ValueError("missing_terminal_event")
    return terminal_text.encode("utf-8")


def _invoke_codex(
    *,
    runner: CommandRunner,
    capabilities: AgentCapabilities,
    settings: ProviderAdapterSettings,
    request: AgentRequest,
    mode: str,
    sandbox: str,
    cancellation: CancellationToken | None,
) -> AgentResponse:
    model_or_failure = _validated_model_or_failure(request)
    model = model_or_failure if isinstance(model_or_failure, str) else None
    identity = identity_for(capabilities, model)
    failure = model_or_failure if isinstance(model_or_failure, AgentFailure) else None
    failure = failure or request_compatibility_failure(capabilities, request)
    failure = failure or validate_invocation_request(request, settings=settings)
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
        prompt = deterministic_prompt(request, mode=mode)
    except ValueError:
        return prelaunch_failure_response(
            request,
            identity=identity,
            failure=_invalid_request("provider_prompt_too_large", "provider prompt is too large"),
        )
    arguments = build_codex_arguments(sandbox=sandbox, model=model)
    sensitive_failure = sensitive_material_failure(prompt, arguments, settings)
    if sensitive_failure is not None:
        return prelaunch_failure_response(request, identity=identity, failure=sensitive_failure)
    result = invoke_command(
        runner,
        request,
        executable=CODEX_EXECUTABLE,
        arguments=arguments,
        prompt=prompt,
        settings=settings,
        cancellation=cancellation,
    )
    normalized = command_failure_response(result, request, identity=identity)
    if normalized is not None:
        return normalized
    try:
        envelope = parse_codex_jsonl(result.stdout.text)
    except ValueError as error:
        duration = min(86_400_000, max(0, int(result.duration_seconds * 1_000)))
        return invalid_output_response(
            request,
            identity=identity,
            started_at=result.started_at,
            completed_at=result.started_at + timedelta(milliseconds=duration),
            category=AgentFailureCategory.MALFORMED_OUTPUT,
            code=str(error),
            message="Codex JSONL output did not satisfy the supported event contract",
        )
    return translate_success(
        result,
        request,
        identity=identity,
        envelope=envelope,
        sensitive_values=settings.sensitive_values,
    )


def _parse_json_lines(text: str) -> list[dict[str, object]]:
    if len(text.encode("utf-8")) > 1 * 1_024 * 1_024:
        raise ValueError("provider_output_too_large")
    lines = text.splitlines()
    if not lines or any(not line.strip() for line in lines):
        raise ValueError("malformed_jsonl")
    values: list[dict[str, object]] = []
    for line in lines:
        try:
            parsed = json.loads(
                line,
                object_pairs_hook=_reject_duplicates,
                parse_constant=_reject_constant,
            )
        except (ValueError, json.JSONDecodeError, RecursionError) as error:
            raise ValueError("malformed_jsonl") from error
        if not isinstance(parsed, dict):
            raise ValueError("malformed_provider_event")
        values.append(parsed)
    return values


def _reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _reject_constant(value: str) -> NoReturn:
    raise ValueError(value)


def _detection(
    compatibility: ProviderCompatibility,
    *,
    version: str | None,
    reason: str | None,
    repair: bool,
    probes: tuple[str, ...],
) -> CodexDetection:
    repair_compatibility = compatibility
    repair_reason = reason
    if compatibility is ProviderCompatibility.AVAILABLE and not repair:
        repair_compatibility = ProviderCompatibility.INCOMPATIBLE
        repair_reason = "Codex help does not prove the required workspace-write repair surface"
    reviewer = provider_capabilities(
        provider="codex",
        adapter="codex.reviewer",
        roles=(AgentRole.REVIEWER,),
        read_only=True,
        writes=False,
        repair=False,
        compatibility=compatibility,
        version=version,
        reason=reason,
        surface=CODEX_REVIEW_SURFACE,
    )
    repair_capabilities = provider_capabilities(
        provider="codex",
        adapter="codex.repairer",
        roles=(AgentRole.REPAIRER,),
        read_only=False,
        writes=True,
        repair=True,
        compatibility=repair_compatibility,
        version=version,
        reason=repair_reason,
        surface=CODEX_REPAIR_SURFACE,
    )
    return CodexDetection(
        compatibility=compatibility,
        version=version,
        reason=reason,
        reviewer_capabilities=reviewer,
        repair_capabilities=repair_capabilities,
        repair_surface_verified=repair,
        probes=probes,
    )


def _contains(help_text: str, tokens: tuple[str, ...]) -> bool:
    folded = help_text.casefold()
    return all(token.casefold() in folded for token in tokens)


def _validated_model_or_failure(request: AgentRequest) -> str | AgentFailure | None:
    try:
        return validate_model_id(request.routing.model)
    except ValueError:
        return _invalid_request("invalid_model_identifier", "provider model identifier is invalid")


def _safe_model(request: AgentRequest) -> str | None:
    try:
        return validate_model_id(request.routing.model)
    except ValueError:
        return None


def _invalid_request(code: str, message: str) -> AgentFailure:
    return AgentFailure(
        category=AgentFailureCategory.INVALID_REQUEST,
        code=code,
        message=message,
        retry=RetryDisposition.NOT_RETRYABLE,
        side_effects=SideEffectState.NONE,
    )
