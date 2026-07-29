from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from revanent.utilities import tool_detection
from revanent.utilities.tool_detection import CheckStatus


def test_probe_reports_missing_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("revanent.utilities.tool_detection.shutil.which", lambda _: None)

    check = tool_detection._probe("missing", ("--version",), required=False, category="provider")

    assert check.status is CheckStatus.UNAVAILABLE
    assert check.detail == "not found on PATH"


def test_probe_uses_argument_list_and_captures_version(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_which(_: str) -> str:
        return str(Path("tools") / "sample")

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="sample 1.2.3\n", stderr="")

    monkeypatch.setattr("revanent.utilities.tool_detection.shutil.which", fake_which)
    monkeypatch.setattr("revanent.utilities.tool_detection.subprocess.run", fake_run)

    check = tool_detection._probe("sample", ("--version",), required=True, category="runtime")

    assert calls == [[str(Path("tools") / "sample"), "--version"]]
    assert check.status is CheckStatus.AVAILABLE
    assert check.detail == "sample 1.2.3"


def test_detect_environment_has_required_and_optional_checks() -> None:
    checks = {check.name: check for check in tool_detection.detect_environment()}

    assert checks["python"].required is True
    assert checks["git"].required is True
    assert checks["opencode"].required is False
    assert checks["codex"].category == "provider"
