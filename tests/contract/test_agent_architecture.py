"""Architecture constraints for the provider-neutral agent boundary."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import get_type_hints

from revanent.agents.codex import CodexRepairAdapter, CodexReviewerAdapter
from revanent.agents.fake import FakeAgentAdapter, FakeAgentScenario
from revanent.agents.opencode import OpenCodeBuilderAdapter
from revanent.ports.agents import AgentAdapter, AgentRequest, AgentResponse, ScenarioId
from tests.agent_factories import NOW, make_capabilities

ROOT = Path(__file__).resolve().parents[2]
AGENT_FILES = (
    *(ROOT / "src" / "revanent" / "agents").glob("*.py"),
    ROOT / "src" / "revanent" / "ports" / "agents.py",
)


def test_agent_modules_import_no_forbidden_infrastructure() -> None:
    forbidden_roots = {
        "subprocess",
        "socket",
        "urllib",
        "httpx",
        "requests",
        "sqlite3",
    }
    forbidden_revanent = {
        "revanent.cli",
        "revanent.git",
        "revanent.storage",
        "revanent.orchestration",
    }
    for path in AGENT_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = {
            node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        assert not {name.split(".")[0] for name in imports} & forbidden_roots
        assert not {name for name in imports if name in forbidden_revanent}


def test_fake_adapter_has_no_process_network_or_arbitrary_callback_surface() -> None:
    source = (ROOT / "src" / "revanent" / "agents" / "fake.py").read_text(encoding="utf-8")
    for forbidden in ("subprocess", "socket", "requests", "httpx", "callback", "os.system"):
        assert forbidden not in source


def test_agent_boundary_cannot_construct_approval_or_mutate_run_state() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in AGENT_FILES)
    assert "ApprovalGate" not in source
    assert "transition_run" not in source
    assert "RunState" not in source


def test_provider_adapters_have_no_shell_git_network_or_argument_escape_hatches() -> None:
    provider_paths = (
        ROOT / "src" / "revanent" / "agents" / "codex.py",
        ROOT / "src" / "revanent" / "agents" / "opencode.py",
        ROOT / "src" / "revanent" / "agents" / "providers.py",
    )
    source = "\n".join(path.read_text(encoding="utf-8") for path in provider_paths)
    forbidden = (
        "subprocess",
        "os.system",
        "shell=True",
        "extra_args",
        "transition_run",
        "ApprovalGate",
        "git commit",
        "git push",
        "git merge",
        "git reset",
        "git clean",
        "requests",
        "httpx",
        "urllib",
        "socket",
    )
    assert all(item not in source for item in forbidden)
    assert "CommandRunner" in source


def test_live_adapters_satisfy_agent_protocol_shape() -> None:
    for adapter in (OpenCodeBuilderAdapter, CodexReviewerAdapter, CodexRepairAdapter):
        hints = get_type_hints(adapter.invoke)
        assert hints["request"] is AgentRequest
        assert hints["return"] is AgentResponse


def test_public_agent_port_uses_typed_contracts() -> None:
    hints = get_type_hints(AgentAdapter.invoke)
    assert hints["request"] is AgentRequest
    assert hints["return"] is AgentResponse
    assert "Any" not in (ROOT / "src" / "revanent" / "ports" / "agents.py").read_text(
        encoding="utf-8"
    )


def test_fake_adapter_satisfies_agent_protocol_shape() -> None:
    scenario = FakeAgentScenario(
        scenario_id=ScenarioId("empty"),
        capabilities=make_capabilities(),
        default_timestamp=NOW,
    )
    adapter: AgentAdapter = FakeAgentAdapter(scenario)
    assert adapter.capabilities == scenario.capabilities
