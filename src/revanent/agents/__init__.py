"""Provider-independent agent helpers and adapters."""

from revanent.agents.base import (
    AgentOutputError,
    agent_request_digest,
    canonical_json_bytes,
    normalize_agent_output,
    parse_agent_response_envelope,
    request_compatibility_failure,
    validate_agent_response_correlation,
    validate_agent_response_semantics,
)
from revanent.agents.codex import (
    CodexDetection,
    CodexRepairAdapter,
    CodexReviewerAdapter,
    build_codex_arguments,
    detect_codex,
    parse_codex_jsonl,
)
from revanent.agents.fake import (
    FakeAgentAdapter,
    FakeAgentScenario,
    FakeAgentStep,
    ScriptedRawOutputOutcome,
    ScriptedResponseOutcome,
)
from revanent.agents.opencode import (
    OpenCodeBuilderAdapter,
    build_opencode_arguments,
    detect_opencode,
    parse_opencode_jsonl,
)
from revanent.agents.providers import (
    ProviderAdapterSettings,
    ProviderCompatibility,
    ProviderDetection,
)

__all__ = [
    "AgentOutputError",
    "CodexDetection",
    "CodexRepairAdapter",
    "CodexReviewerAdapter",
    "FakeAgentAdapter",
    "FakeAgentScenario",
    "FakeAgentStep",
    "OpenCodeBuilderAdapter",
    "ProviderAdapterSettings",
    "ProviderCompatibility",
    "ProviderDetection",
    "ScriptedRawOutputOutcome",
    "ScriptedResponseOutcome",
    "agent_request_digest",
    "build_codex_arguments",
    "build_opencode_arguments",
    "canonical_json_bytes",
    "detect_codex",
    "detect_opencode",
    "normalize_agent_output",
    "parse_agent_response_envelope",
    "parse_codex_jsonl",
    "parse_opencode_jsonl",
    "request_compatibility_failure",
    "validate_agent_response_correlation",
    "validate_agent_response_semantics",
]
