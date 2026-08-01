from __future__ import annotations

import ast
from pathlib import Path


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    values: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            values.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            values.append(node.module)
    return tuple(values)


def test_cli_is_presentation_only_and_has_no_adapter_dependency() -> None:
    imports = _imports(Path("src/revanent/cli/app.py"))
    forbidden = (
        "subprocess",
        "sqlite3",
        "revanent.storage",
        "revanent.git",
        "revanent.commands",
        "revanent.agents.codex",
        "revanent.agents.opencode",
    )

    assert not any(
        imported == prefix or imported.startswith(prefix + ".")
        for imported in imports
        for prefix in forbidden
    )
    assert any(imported == "revanent.application" for imported in imports)


def test_cli_implements_p6_002_runtime_and_report_workflows_without_cleanup() -> None:
    source = Path("src/revanent/cli/app.py").read_text(encoding="utf-8")

    for command in ("def run(", "def resume(", "def status(", "def cancel(", "def report("):
        assert command in source
    for command in ("def cleanup(",):
        assert command not in source


def test_report_assembly_is_read_only_and_provider_neutral() -> None:
    path = Path("src/revanent/application/reports.py")
    imports = _imports(path)
    source = path.read_text(encoding="utf-8")

    forbidden_imports = (
        "revanent.storage",
        "revanent.agents",
        "revanent.commands",
        "revanent.git",
        "subprocess",
    )
    assert not any(
        imported == prefix or imported.startswith(prefix + ".")
        for imported in imports
        for prefix in forbidden_imports
    )
    for forbidden in (".transition_run(", ".reconcile(", ".settle_reservation(", ".execute("):
        assert forbidden not in source


def test_runtime_application_services_preserve_orchestration_ownership() -> None:
    path = Path("src/revanent/application/workflows.py")
    imports = _imports(path)
    source = path.read_text(encoding="utf-8")
    status_source = source.split("class StatusApplicationService:", 1)[1].split(
        "def _validate_runtime_identity", 1
    )[0]
    resume_source = source.split("class ResumeApplicationService:", 1)[1].split(
        "class CancellationApplicationService:", 1
    )[0]
    cancel_source = source.split("class CancellationApplicationService:", 1)[1].split(
        "class StatusApplicationService:", 1
    )[0]

    assert "revanent.storage" not in imports
    assert ".reconcile(" in resume_source
    assert resume_source.index(".reconcile(") < resume_source.index(".execute(")
    assert ".orchestration.cancel(" in cancel_source
    for forbidden in (".transition_run(", ".settle_reservation(", ".reconcile(", ".execute("):
        assert forbidden not in status_source


def test_runtime_composition_authorizes_only_the_configured_worktree_root() -> None:
    source = Path("src/revanent/application/runtime_composition.py").read_text(encoding="utf-8")

    assert source.count("worktree_root=paths.workspace_root") == 2
    assert "worktree_root=effective.repository_root" not in source


def test_project_configuration_uses_safe_loader_and_no_environment_overlay() -> None:
    source = Path("src/revanent/config/loader.py").read_text(encoding="utf-8")
    project_source = Path("src/revanent/config/project.py").read_text(encoding="utf-8")

    assert "yaml.safe_load" in source
    assert "os.environ" not in project_source
    assert "while True" not in project_source


def test_initialization_uses_no_overwrite_or_git_mutation_surface() -> None:
    source = Path("src/revanent/application/initialization.py").read_text(encoding="utf-8")

    for forbidden in ("os.replace", "--force", "git commit", "git push", "git merge"):
        assert forbidden not in source
    assert "write_text" not in source
    assert "os.link" in source
