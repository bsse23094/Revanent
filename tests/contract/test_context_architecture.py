from __future__ import annotations

import ast
from pathlib import Path


def test_context_boundary_has_no_provider_process_storage_git_or_network_dependency() -> None:
    forbidden = (
        "subprocess",
        "sqlite3",
        "revanent.agents.codex",
        "revanent.agents.opencode",
        "revanent.git.local",
        "revanent.storage.sqlite",
        "requests",
        "httpx",
    )
    for path in Path("src/revanent/context").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        ]
        assert not any(
            name == prefix or name.startswith(prefix + ".")
            for name in imports
            for prefix in forbidden
        )


def test_context_port_has_no_implementation_or_external_io_dependency() -> None:
    path = Path("src/revanent/ports/context.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = [
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    ]
    forbidden = (
        "revanent.context",
        "revanent.commands",
        "revanent.orchestration",
        "revanent.storage",
        "revanent.git",
        "subprocess",
        "sqlite3",
        "requests",
        "httpx",
    )
    assert not any(
        imported == prefix or imported.startswith(prefix + ".")
        for imported in imports
        for prefix in forbidden
    )


def test_context_filesystem_reads_are_confined_to_the_reader() -> None:
    for path in Path("src/revanent/context").glob("*.py"):
        if path.name == "reader.py":
            continue
        source = path.read_text(encoding="utf-8")
        assert ".open(" not in source
        assert ".read_text(" not in source
        assert ".read_bytes(" not in source
        assert ".rglob(" not in source
