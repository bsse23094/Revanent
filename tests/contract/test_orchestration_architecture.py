from __future__ import annotations

import ast
from pathlib import Path

ORCHESTRATION_ROOT = Path("src/revanent/orchestration")
ORCHESTRATION_FILES = tuple(sorted(ORCHESTRATION_ROOT.glob("*.py")))


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.append(node.module)
    return tuple(names)


def test_orchestration_depends_only_on_ports_domain_and_pure_services() -> None:
    forbidden = (
        "sqlite3",
        "subprocess",
        "typer",
        "revanent.agents.codex",
        "revanent.agents.opencode",
        "revanent.commands.local",
        "revanent.git.local",
        "revanent.storage.sqlite",
        "revanent.cli",
    )

    for path in ORCHESTRATION_FILES:
        imports = _imports(path)
        assert not any(
            imported == prefix or imported.startswith(prefix + ".")
            for imported in imports
            for prefix in forbidden
        ), path


def test_only_coordinator_imports_authoritative_transition_function() -> None:
    transition_importers = {
        path.name
        for path in ORCHESTRATION_FILES
        if "transition_run" in path.read_text(encoding="utf-8")
    }

    assert transition_importers == {"service.py"}


def test_orchestration_has_no_shell_network_destructive_git_or_unbounded_loop_surface() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in ORCHESTRATION_FILES)
    forbidden = (
        "shell=True",
        "os.environ",
        "while True",
        "git reset",
        "git clean",
        "git push",
        "git merge",
        "git fetch",
        "git pull",
        "git commit",
        "cleanup_worktree(",
    )

    assert all(token not in source for token in forbidden)


def test_coordinator_never_constructs_provider_or_approval_authority() -> None:
    source = (ORCHESTRATION_ROOT / "service.py").read_text(encoding="utf-8")

    assert "ApprovalGate(" not in source
    assert "OpenCode" not in source
    assert "CodexAgent" not in source
    assert "CodexReview" not in source
