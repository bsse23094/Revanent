from __future__ import annotations

import ast
from pathlib import Path


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.append(node.module)
    return tuple(imports)


def test_subprocess_is_confined_to_controlled_adapter() -> None:
    source_root = Path("src/revanent")
    direct_imports = {
        path.as_posix() for path in source_root.rglob("*.py") if "subprocess" in _imports(path)
    }

    assert direct_imports == {"src/revanent/commands/local.py"}


def test_domain_and_ports_do_not_import_command_infrastructure() -> None:
    protected = tuple(Path("src/revanent/domain").rglob("*.py")) + tuple(
        Path("src/revanent/ports").rglob("*.py")
    )

    for path in protected:
        imports = _imports(path)
        assert not any(name.startswith("revanent.commands") for name in imports), path


def test_production_process_launch_is_explicitly_shell_free() -> None:
    tree = ast.parse(Path("src/revanent/commands/local.py").read_text(encoding="utf-8"))
    popen_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "Popen"
    ]

    assert len(popen_calls) == 2
    for call in popen_calls:
        shell = next(keyword.value for keyword in call.keywords if keyword.arg == "shell")
        assert isinstance(shell, ast.Constant)
        assert shell.value is False
