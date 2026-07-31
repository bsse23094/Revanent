from __future__ import annotations

import ast
from pathlib import Path
from typing import cast

from revanent.ports import (
    GitRepository,
    RepositoryIdentity,
    WorktreeCreationRequest,
    WorktreeOwnershipRecord,
)
from revanent.ports.git import GIT_SCHEMA_VERSION, OWNERSHIP_SCHEMA_VERSION


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.append(node.module)
    return tuple(imports)


def test_git_contract_schema_versions_are_frozen_at_one() -> None:
    assert GIT_SCHEMA_VERSION == 1
    assert OWNERSHIP_SCHEMA_VERSION == 1
    assert RepositoryIdentity.model_json_schema()["additionalProperties"] is False
    assert WorktreeCreationRequest.model_json_schema()["additionalProperties"] is False
    assert WorktreeOwnershipRecord.model_json_schema()["additionalProperties"] is False


def test_git_port_exposes_typed_methods_without_command_results() -> None:
    annotations = GitRepository.__dict__

    assert {
        "discover",
        "inspect",
        "create_worktree",
        "verify_owned_worktree",
        "cleanup_worktree",
    } <= (annotations.keys())
    source = Path("src/revanent/ports/git.py").read_text(encoding="utf-8")
    assert "CommandResult" not in source
    assert "subprocess" not in source


def test_domain_and_git_port_do_not_import_git_infrastructure() -> None:
    protected = (
        *Path("src/revanent/domain").rglob("*.py"),
        Path("src/revanent/ports/git.py"),
    )

    for path in protected:
        imports = _imports(path)
        assert not any(name.startswith("revanent.git") for name in imports), path
        assert "subprocess" not in imports, path


def test_local_git_adapter_has_no_direct_process_or_shell_boundary() -> None:
    production = tuple(Path("src/revanent/git").rglob("*.py"))

    for path in production:
        imports = _imports(path)
        assert "subprocess" not in imports, path
        assert "shutil" not in imports, path
        source = path.read_text(encoding="utf-8")
        assert "shell=True" not in source
        assert "os.system" not in source


def test_local_git_mutation_surface_contains_only_add_and_normal_remove() -> None:
    tree = ast.parse(Path("src/revanent/git/local.py").read_text(encoding="utf-8"))
    literal_tuples = {
        tuple(cast(str, cast(ast.Constant, element).value) for element in node.elts)
        for node in ast.walk(tree)
        if isinstance(node, ast.Tuple)
        and node.elts
        and all(
            isinstance(element, ast.Constant) and isinstance(element.value, str)
            for element in node.elts
        )
    }

    first_words = {values[0] for values in literal_tuples}
    assert (
        not {"push", "fetch", "pull", "merge", "rebase", "reset", "clean", "commit"} & first_words
    )
    assert not any(values[:2] == ("worktree", "prune") for values in literal_tuples)
    assert not any(values[:2] == ("branch", "-D") for values in literal_tuples)


def test_ownership_storage_is_separate_from_sqlite_run_storage() -> None:
    imports = _imports(Path("src/revanent/git/ownership.py"))

    assert "sqlite3" not in imports
    assert not any(name.startswith("revanent.storage") for name in imports)


def test_git_filesystem_deletion_is_limited_to_owned_lock_and_temp_files() -> None:
    calls: list[tuple[Path, ast.Call]] = []
    for path in Path("src/revanent/git").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        calls.extend(
            (path, node)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"unlink", "rmdir", "remove", "removedirs", "rmtree"}
        )

    assert len(calls) == 1
    path, call = calls[0]
    assert path.as_posix() == "src/revanent/git/ownership.py"
    assert isinstance(call.func, ast.Attribute)
    assert call.func.attr == "unlink"
