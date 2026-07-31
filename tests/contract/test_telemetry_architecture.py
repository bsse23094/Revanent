from __future__ import annotations

import ast
import inspect
from pathlib import Path

from revanent.orchestration import OrchestrationService

TELEMETRY_FILES = (
    Path("src/revanent/ports/telemetry.py"),
    *sorted(Path("src/revanent/telemetry").glob("*.py")),
)


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.append(node.module)
    return tuple(names)


def test_telemetry_has_no_storage_process_git_network_or_provider_implementation_dependency() -> (
    None
):
    forbidden = (
        "sqlite3",
        "subprocess",
        "socket",
        "requests",
        "httpx",
        "urllib",
        "revanent.storage",
        "revanent.git",
        "revanent.commands.local",
        "revanent.agents.codex",
        "revanent.agents.opencode",
    )
    for path in TELEMETRY_FILES:
        imports = _imports(path)
        assert not any(
            imported == prefix or imported.startswith(prefix + ".")
            for imported in imports
            for prefix in forbidden
        ), path


def test_telemetry_has_no_transition_sql_network_pricing_or_unbounded_retry_surface() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in TELEMETRY_FILES)
    forbidden = (
        "transition_run",
        "sqlite",
        "SELECT ",
        "INSERT ",
        "UPDATE ",
        "DELETE ",
        "while True",
        "sleep(",
        "urlopen",
        "current_price",
        "pricing_api",
    )

    assert all(token not in source for token in forbidden)


def test_orchestration_requires_telemetry_without_silent_optional_fallback() -> None:
    parameter = inspect.signature(OrchestrationService).parameters["telemetry"]

    assert parameter.default is inspect.Parameter.empty
    assert parameter.annotation != "TelemetryService | None"


def test_persisted_telemetry_schema_is_metadata_only() -> None:
    source = Path("src/revanent/storage/sqlite.py").read_text(encoding="utf-8")
    forbidden_columns = (
        "prompt_text",
        "context_body",
        "source_code",
        "raw_provider_output",
        "command_output",
        "authorization_header",
        "environment_value",
        "home_path",
        "host_metadata",
    )

    assert all(column not in source for column in forbidden_columns)
