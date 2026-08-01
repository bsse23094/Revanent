from __future__ import annotations

from pathlib import Path

from revanent.application.runtime import controlled_host_runner
from revanent.ports import CommandRequest, CommandStatus


def _stdin_request(root: Path, correlation: str) -> CommandRequest:
    return CommandRequest(
        executable="python",
        arguments=("-c", "import sys; print(sys.stdin.read())"),
        working_directory=root,
        correlation_id=correlation,
        timeout_seconds=10,
        stdin=b"bounded-live-prompt",
    )


def test_provider_stdin_is_default_off_and_requires_explicit_composition(
    tmp_path: Path,
) -> None:
    denied = controlled_host_runner(tmp_path, ("python",)).run(
        _stdin_request(tmp_path, "live-stdin-denied")
    )
    allowed = controlled_host_runner(
        tmp_path,
        ("python",),
        allow_provider_stdin=True,
    ).run(_stdin_request(tmp_path, "live-stdin-allowed"))

    assert denied.status is CommandStatus.POLICY_REJECTED
    assert allowed.status is CommandStatus.SUCCESS
    assert allowed.stdout.text.strip() == "bounded-live-prompt"
