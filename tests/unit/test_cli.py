import pytest
from typer.testing import CliRunner

from revanent.cli import app as cli_app
from revanent.cli.app import app
from revanent.utilities.tool_detection import CheckStatus, ToolCheck

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.startswith("revanent ")


def test_help_lists_doctor() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "doctor" in result.stdout


def test_doctor_allows_missing_optional_provider_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checks = (
        ToolCheck("python", "runtime", CheckStatus.AVAILABLE, "CPython", True),
        ToolCheck("opencode", "provider", CheckStatus.UNAVAILABLE, "not found", False),
    )
    monkeypatch.setattr(cli_app, "detect_environment", lambda: checks)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "unavailable" in result.stdout


def test_doctor_strict_rejects_missing_optional_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    checks = (
        ToolCheck("python", "runtime", CheckStatus.AVAILABLE, "CPython", True),
        ToolCheck("opencode", "provider", CheckStatus.UNAVAILABLE, "not found", False),
    )
    monkeypatch.setattr(cli_app, "detect_environment", lambda: checks)

    result = runner.invoke(app, ["doctor", "--strict"])

    assert result.exit_code == 1
