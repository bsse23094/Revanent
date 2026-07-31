from __future__ import annotations

import ctypes
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Thread

import pytest

from revanent.commands import (
    CancellationSource,
    CommandPolicy,
    EnvironmentPolicy,
    ExecutablePolicy,
    ExecutableRule,
    LocalCommandRunner,
    PathPolicy,
    Redactor,
)
from revanent.ports import (
    ArtifactStatus,
    CommandFailureCategory,
    CommandRequest,
    CommandResult,
    CommandStatus,
    EnvironmentOverrides,
    OutputLimits,
)

FAKE_COMMAND = Path(__file__).parents[1] / "fixtures" / "fake_command.py"


def _baseline_environment() -> dict[str, str]:
    names = ("SYSTEMROOT", "WINDIR") if os.name == "nt" else ()
    return {name: os.environ[name] for name in names if name in os.environ}


def _runner(
    root: Path,
    *,
    artifact_root: Path | None = None,
    secrets: tuple[str, ...] = (),
) -> LocalCommandRunner:
    executable = Path(sys.executable).resolve(strict=True)
    extensions = (executable.suffix,) if os.name == "nt" else ()
    return LocalCommandRunner(
        executable_policy=ExecutablePolicy(
            (
                ExecutableRule(
                    "fixture-python",
                    (executable,),
                    allowed_extensions=extensions,
                ),
            )
        ),
        path_policy=PathPolicy(
            (root.resolve(strict=True),),
            artifact_roots=(artifact_root.resolve(strict=True),) if artifact_root else (),
        ),
        environment_policy=EnvironmentPolicy(
            _baseline_environment(),
            allowed_override_keys=frozenset({"SAFE", "UNIQUE", "API_TOKEN"}),
            forbidden_keys=frozenset({"HOME", "USERPROFILE", "SSH_AUTH_SOCK"}),
            allowed_sensitive_keys=frozenset({"API_TOKEN"}),
        ),
        command_policy=CommandPolicy(allow_stdin=True),
        redactor=Redactor(secrets),
        poll_interval_seconds=0.005,
        termination_grace_seconds=0.5,
    )


def _request(
    root: Path,
    mode: str,
    *arguments: str,
    correlation_id: str = "fixture-command",
    **changes: object,
) -> CommandRequest:
    values: dict[str, object] = {
        "executable": "fixture-python",
        "arguments": (str(FAKE_COMMAND), mode, *arguments),
        "working_directory": root.resolve(strict=True),
        "correlation_id": correlation_id,
        "timeout_seconds": 2.0,
    }
    values.update(changes)
    if isinstance(values.get("environment"), dict):
        values["environment"] = EnvironmentOverrides.from_mapping(values["environment"])  # type: ignore[arg-type]
    return CommandRequest(**values)  # type: ignore[arg-type]


def _wait_for(path: Path, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert path.exists()


def _process_is_running(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    process_query_limited_information = 0x1000
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == 259
    finally:
        kernel32.CloseHandle(handle)


def test_arguments_spaces_and_shell_metacharacters_remain_literal(tmp_path: Path) -> None:
    root = tmp_path / "root with spaces"
    root.mkdir()
    arguments = ("with spaces", "; touch escaped", "&& exit 9", "$(echo injected)", "%PATH%")

    result = _runner(root).run(_request(root, "args", *arguments))

    assert result.status is CommandStatus.SUCCESS
    assert json.loads(result.stdout.text) == list(arguments)
    assert not (root / "escaped").exists()
    assert result.resolved_executable == Path(sys.executable).resolve(strict=True)


def test_explicit_working_directory_with_spaces_is_used(tmp_path: Path) -> None:
    root = tmp_path / "working directory with spaces"
    root.mkdir()

    result = _runner(root).run(_request(root, "cwd"))

    assert Path(result.stdout.text.strip()) == root.resolve(strict=True)


def test_child_environment_contains_only_baseline_and_approved_overrides(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()

    result = _runner(root).run(_request(root, "env", environment={"SAFE": "command-value"}))
    child = json.loads(result.stdout.text)

    expected = {key.upper() if os.name == "nt" else key for key in _baseline_environment()}
    assert set(child) == expected | {"SAFE"}
    assert child["SAFE"] == "command-value"
    assert "HOME" not in child
    assert "USERPROFILE" not in child


def test_forbidden_environment_is_normalized_as_policy_rejection(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()

    result = _runner(root).run(_request(root, "env", environment={"HOME": "blocked"}))

    assert result.status is CommandStatus.POLICY_REJECTED
    assert result.failure is not None
    assert result.failure.category is CommandFailureCategory.ENVIRONMENT
    assert "blocked" not in str(result)


def test_disallowed_executable_is_normalized_as_policy_rejection(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()

    result = _runner(root).run(
        CommandRequest(
            executable="not-allowed",
            arguments=(),
            working_directory=root.resolve(),
            correlation_id="disallowed-executable",
        )
    )

    assert result.status is CommandStatus.POLICY_REJECTED
    assert result.failure is not None
    assert result.failure.category is CommandFailureCategory.POLICY


def test_success_and_nonzero_exit_are_structured_results(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    runner = _runner(root)

    success = runner.run(_request(root, "exit", "0", correlation_id="success"))
    failure = runner.run(_request(root, "exit", "7", correlation_id="failure"))
    expected = runner.run(
        _request(
            root,
            "exit",
            "7",
            correlation_id="expected-seven",
            expected_exit_codes=(7,),
        )
    )

    assert success.status is CommandStatus.SUCCESS
    assert success.exit_code == 0
    assert success.started_at.tzinfo is not None
    assert success.completed_at >= success.started_at
    assert success.duration_seconds >= 0
    assert failure.status is CommandStatus.NONZERO_EXIT
    assert failure.exit_code == 7
    assert failure.failure is None
    assert expected.status is CommandStatus.SUCCESS


def test_launch_failure_is_sanitized_and_normalized(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    invalid = tmp_path / ("invalid.exe" if os.name == "nt" else "invalid")
    invalid.write_text("not an executable format", encoding="ascii")
    if os.name != "nt":
        invalid.chmod(0o700)
    runner = LocalCommandRunner(
        executable_policy=ExecutablePolicy(
            (
                ExecutableRule(
                    "invalid",
                    (invalid.resolve(),),
                    allowed_extensions=(".exe",) if os.name == "nt" else (),
                ),
            )
        ),
        path_policy=PathPolicy((root.resolve(),)),
        environment_policy=EnvironmentPolicy(_baseline_environment()),
        redactor=Redactor(("not an executable format",)),
    )

    result = runner.run(
        CommandRequest(
            executable="invalid",
            arguments=(),
            working_directory=root.resolve(),
            correlation_id="launch-failure",
        )
    )

    assert result.status is CommandStatus.LAUNCH_FAILED
    assert result.failure is not None
    assert result.failure.category is CommandFailureCategory.LAUNCH
    assert "not an executable format" not in str(result)


def test_unexpected_runner_failure_is_sanitized_and_normalized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    runner = _runner(root, secrets=("internal-secret",))

    def fail_launch(*_: object, **__: object) -> None:
        raise RuntimeError("internal-secret")

    monkeypatch.setattr(runner, "_launch", fail_launch)
    result = runner.run(_request(root, "exit", "0"))

    assert result.status is CommandStatus.INTERNAL_ERROR
    assert result.failure is not None
    assert result.failure.category is CommandFailureCategory.INTERNAL
    assert "internal-secret" not in repr(result)


def test_timeout_terminates_direct_child_and_cleans_streams(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    launched = root / "launched.pid"
    release = root / "release"

    result = _runner(root).run(
        _request(
            root,
            "block",
            str(launched),
            str(release),
            timeout_seconds=0.5,
        )
    )

    pid = int(launched.read_text(encoding="ascii"))
    assert result.status is CommandStatus.TIMEOUT
    assert result.failure is not None
    assert result.failure.category is CommandFailureCategory.TIMEOUT
    assert result.duration_seconds < 2
    assert not _process_is_running(pid)


def test_precancelled_request_never_launches(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    marker = root / "must-not-exist"
    cancellation = CancellationSource()
    cancellation.cancel()

    result = _runner(root).run(
        _request(root, "write-marker", str(marker), cancellation=cancellation)
    )

    assert result.status is CommandStatus.CANCELLED
    assert not marker.exists()


def test_mid_execution_cancellation_terminates_direct_child(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    launched = root / "launched.pid"
    release = root / "release"
    cancellation = CancellationSource()
    results: list[CommandResult] = []
    thread = Thread(
        target=lambda: results.append(
            _runner(root).run(
                _request(
                    root,
                    "block",
                    str(launched),
                    str(release),
                    cancellation=cancellation,
                )
            )
        )
    )
    thread.start()
    _wait_for(launched)

    cancellation.cancel()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert results[0].status is CommandStatus.CANCELLED
    assert not _process_is_running(int(launched.read_text(encoding="ascii")))


def test_cancellation_wins_a_simultaneous_timeout_check_deterministically(
    tmp_path: Path,
) -> None:
    class CancelAfterPrelaunch:
        checks = 0

        def is_cancelled(self) -> bool:
            self.checks += 1
            return self.checks > 1

    root = tmp_path / "root"
    root.mkdir()
    launched = root / "launched.pid"

    result = _runner(root).run(
        _request(
            root,
            "block",
            str(launched),
            str(root / "release"),
            timeout_seconds=0.001,
            cancellation=CancelAfterPrelaunch(),
        )
    )

    assert result.status is CommandStatus.CANCELLED


def test_cancellation_after_completion_does_not_rewrite_result(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    cancellation = CancellationSource()

    result = _runner(root).run(_request(root, "exit", "0", cancellation=cancellation))
    cancellation.cancel()

    assert result.status is CommandStatus.SUCCESS


def test_stdout_and_stderr_are_separate_and_independently_truncated(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    limits = OutputLimits(stdout_bytes=5, stderr_bytes=3, artifact_bytes_per_stream=32)

    result = _runner(root).run(
        _request(root, "streams", "abcdefgh", "123456", output_limits=limits)
    )

    assert result.stdout.text.startswith("abcde")
    assert result.stderr.text.startswith("123")
    assert "123" not in result.stdout.text
    assert "abcde" not in result.stderr.text
    assert result.stdout.observed_bytes == 8
    assert result.stdout.retained_bytes == 5
    assert result.stdout.truncated is True
    assert result.stderr.observed_bytes == 6
    assert result.stderr.retained_bytes == 3
    assert result.stderr.truncated is True


def test_large_simultaneous_streams_do_not_deadlock_or_cross_contaminate(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    byte_count = 2 * 1_024 * 1_024
    limits = OutputLimits(
        stdout_bytes=1_024,
        stderr_bytes=2_048,
        artifact_bytes_per_stream=4_096,
    )

    result = _runner(root).run(
        _request(root, "flood", str(byte_count), output_limits=limits, timeout_seconds=3)
    )

    assert result.status is CommandStatus.SUCCESS
    assert result.stdout.observed_bytes == byte_count
    assert result.stderr.observed_bytes == byte_count
    assert result.stdout.retained_bytes == 1_024
    assert result.stderr.retained_bytes == 2_048
    assert result.stdout.text.startswith("x" * 100)
    assert result.stderr.text.startswith("y" * 100)


def test_invalid_output_bytes_decode_with_replacement(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()

    result = _runner(root).run(_request(root, "invalid-bytes"))

    assert result.stdout.text == "valid\ufffdtail"
    assert result.stderr.text == "error\ufffdtail"
    assert result.stdout.observed_bytes == 10
    assert result.stderr.observed_bytes == 10


def test_stdin_is_bounded_and_delivered_without_text_transformation(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    payload = b"line one\x00line two\xff"

    result = _runner(root).run(_request(root, "stdin", stdin=payload))

    assert result.stdout.text == payload.decode("utf-8", errors="replace")
    assert result.stdout.observed_bytes == len(payload)


def test_overflow_artifacts_are_contained_bounded_atomic_and_redacted(tmp_path: Path) -> None:
    root = tmp_path / "root with spaces"
    artifacts = root / ".revanent" / "runs" / "run with spaces"
    artifacts.mkdir(parents=True)
    secret = "configured-super-secret"
    limits = OutputLimits(stdout_bytes=8, stderr_bytes=8, artifact_bytes_per_stream=64)

    result = _runner(root, artifact_root=artifacts, secrets=(secret,)).run(
        _request(
            root,
            "streams",
            f"before-{secret}-after",
            f"error-{secret}-tail",
            output_limits=limits,
            artifact_directory=artifacts.resolve(),
            correlation_id="redacted-overflow",
        )
    )

    for captured in (result.stdout, result.stderr):
        assert secret not in captured.text
        assert captured.artifact is not None
        assert captured.artifact.status is ArtifactStatus.COMPLETE
        assert captured.artifact.path.parent == artifacts.resolve()
        assert captured.artifact.stored_bytes <= 64
        artifact_text = captured.artifact.path.read_text(encoding="utf-8")
        assert secret not in artifact_text
        assert "[REDACTED]" in artifact_text
    assert list(artifacts.glob("*.tmp")) == []


def test_artifact_size_limit_is_reported_as_truncated(tmp_path: Path) -> None:
    root = tmp_path / "root"
    artifacts = root / "artifacts"
    artifacts.mkdir(parents=True)
    limits = OutputLimits(stdout_bytes=4, stderr_bytes=4, artifact_bytes_per_stream=32)

    result = _runner(root, artifact_root=artifacts).run(
        _request(
            root,
            "streams",
            "x" * 100,
            "",
            output_limits=limits,
            artifact_directory=artifacts.resolve(),
        )
    )

    assert result.stdout.artifact is not None
    assert result.stdout.artifact.status is ArtifactStatus.TRUNCATED
    assert result.stdout.artifact.observed_bytes == 100
    assert result.stdout.artifact.source_bytes_retained == 32
    assert result.stdout.artifact.stored_bytes <= 32


def test_redaction_expansion_is_bounded_and_reported(tmp_path: Path) -> None:
    root = tmp_path / "root"
    artifacts = root / "artifacts"
    artifacts.mkdir(parents=True)
    limits = OutputLimits(stdout_bytes=8, stderr_bytes=8, artifact_bytes_per_stream=16)

    result = _runner(root, artifact_root=artifacts, secrets=("abcd",)).run(
        _request(
            root,
            "streams",
            "abcdabcd",
            "",
            output_limits=limits,
            artifact_directory=artifacts.resolve(),
            correlation_id="redaction-expansion",
        )
    )

    assert result.stdout.truncated is False
    assert result.stdout.redaction_truncated is True
    assert len(result.stdout.text.encode("utf-8")) < 128
    assert result.stdout.artifact is not None
    assert result.stdout.artifact.status is ArtifactStatus.TRUNCATED
    assert result.stdout.artifact.source_bytes_retained == 8
    assert result.stdout.artifact.redacted_bytes_observed > 16
    assert result.stdout.artifact.stored_bytes <= 16


def test_sensitive_environment_secret_is_removed_from_every_public_surface(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    secret = "environment-secret-value"

    request = _request(
        root,
        "selected-env",
        "API_TOKEN",
        environment={"API_TOKEN": secret},
    )
    result = _runner(root).run(request)

    assert secret not in repr(request)
    assert secret not in repr(result)
    assert secret not in result.stdout.text
    assert secret not in result.stderr.text
    assert secret not in caplog.text
    assert "[REDACTED]" in result.stdout.text


def test_output_artifact_failure_is_explicit_and_keeps_no_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    artifacts = root / "artifacts"
    artifacts.mkdir(parents=True)
    runner = _runner(root, artifact_root=artifacts)

    def fail_write(_: Path, __: bytes) -> None:
        raise OSError("fixture write failure")

    monkeypatch.setattr(runner, "_atomic_write", fail_write)
    result = runner.run(
        _request(
            root,
            "streams",
            "overflow",
            "",
            output_limits=OutputLimits(
                stdout_bytes=2,
                stderr_bytes=2,
                artifact_bytes_per_stream=16,
            ),
            artifact_directory=artifacts.resolve(),
        )
    )

    assert result.status is CommandStatus.OUTPUT_ARTIFACT_FAILED
    assert result.failure is not None
    assert result.failure.category is CommandFailureCategory.OUTPUT_ARTIFACT
    assert result.stdout.artifact is None


def test_concurrent_commands_do_not_leak_output_or_environments(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    runner = _runner(root)

    def invoke(index: int) -> CommandResult:
        value = f"value-{index}"
        return runner.run(
            _request(
                root,
                "selected-env",
                "UNIQUE",
                environment={"UNIQUE": value},
                correlation_id=f"concurrent-{index}",
            )
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(invoke, range(16)))

    for index, result in enumerate(results):
        assert result.status is CommandStatus.SUCCESS
        assert json.loads(result.stdout.text) == {"UNIQUE": f"value-{index}"}
        assert result.stdout.text.strip() == json.dumps(
            {"UNIQUE": f"value-{index}"}, sort_keys=True
        )
