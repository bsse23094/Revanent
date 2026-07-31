"""Architecture constraints for validation execution and local approval gates."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import get_type_hints

from revanent.ports.validation import (
    ValidationExecutor,
    ValidationPlan,
    ValidationPlanResult,
)

ROOT = Path(__file__).resolve().parents[2]
VALIDATION_PATHS = (
    ROOT / "src" / "revanent" / "ports" / "validation.py",
    ROOT / "src" / "revanent" / "validation" / "runner.py",
    ROOT / "src" / "revanent" / "review" / "gates.py",
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)} | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }


def test_validation_and_gate_modules_import_no_concrete_infrastructure() -> None:
    forbidden_roots = {"subprocess", "socket", "urllib", "httpx", "requests", "sqlite3"}
    forbidden_revanent = {
        "revanent.agents.codex",
        "revanent.agents.opencode",
        "revanent.cli",
        "revanent.commands.local",
        "revanent.git",
        "revanent.storage",
        "revanent.orchestration",
    }
    for path in VALIDATION_PATHS:
        imports = _imports(path)
        assert not {name.split(".")[0] for name in imports} & forbidden_roots
        assert not {name for name in imports if name in forbidden_revanent}


def test_domain_and_agent_providers_do_not_import_validation_or_review_gate() -> None:
    protected_paths = (
        *(ROOT / "src" / "revanent" / "domain").glob("*.py"),
        ROOT / "src" / "revanent" / "agents" / "codex.py",
        ROOT / "src" / "revanent" / "agents" / "opencode.py",
    )
    for path in protected_paths:
        imports = _imports(path)
        assert not {
            name
            for name in imports
            if name.startswith("revanent.validation") or name.startswith("revanent.review")
        }


def test_gate_and_runner_have_no_orchestration_git_or_approval_bypass_surface() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in VALIDATION_PATHS)
    forbidden = (
        "transition_run",
        "RunState",
        "extra_args",
        "shell=True",
        "subprocess",
        "os.environ",
        "model_construct",
        "git commit",
        "git push",
        "git merge",
        "git reset",
        "git clean",
        "git stash",
        "git rebase",
        "git fetch",
        "git pull",
    )
    assert all(item not in source for item in forbidden)


def test_only_local_review_gate_constructs_approval_gate() -> None:
    provider_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "src" / "revanent" / "agents").glob("*.py")
    )
    gate_source = (ROOT / "src" / "revanent" / "review" / "gates.py").read_text(encoding="utf-8")
    assert "ApprovalGate(" not in provider_source
    assert "ApprovalGate(" in gate_source


def test_validation_executor_port_uses_typed_provider_neutral_contracts() -> None:
    hints = get_type_hints(ValidationExecutor.execute)
    assert hints["plan"] is ValidationPlan
    assert hints["return"] is ValidationPlanResult
    port_source = (ROOT / "src" / "revanent" / "ports" / "validation.py").read_text(
        encoding="utf-8"
    )
    assert "revanent.commands" not in port_source
    assert "LocalCommandRunner" not in port_source


def test_review_gate_has_no_process_agent_or_persistence_invocation() -> None:
    source = (ROOT / "src" / "revanent" / "review" / "gates.py").read_text(encoding="utf-8")
    for forbidden in (
        "CommandRunner",
        ".invoke(",
        ".run(",
        ".save(",
        ".transition(",
        "retry",
    ):
        assert forbidden not in source
