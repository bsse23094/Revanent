"""Controlled local subprocess adapter; the only production process-launch boundary."""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Thread
from typing import IO

from revanent.commands.policy import CommandPolicy, EnvironmentPolicy, ExecutablePolicy, PathPolicy
from revanent.commands.redaction import Redactor
from revanent.ports.commands import (
    ArtifactReference,
    ArtifactStatus,
    CapturedOutput,
    CommandFailure,
    CommandFailureCategory,
    CommandPolicyError,
    CommandRequest,
    CommandResult,
    CommandStatus,
    OutputStream,
)

_READ_SIZE = 64 * 1_024


@dataclass(slots=True)
class _StreamCollector:
    capture_limit: int
    artifact_limit: int
    captured: bytearray = field(default_factory=bytearray)
    artifact: bytearray = field(default_factory=bytearray)
    observed: int = 0

    def add(self, chunk: bytes) -> None:
        self.observed += len(chunk)
        capture_remaining = self.capture_limit - len(self.captured)
        if capture_remaining > 0:
            self.captured.extend(chunk[:capture_remaining])
        artifact_remaining = self.artifact_limit - len(self.artifact)
        if artifact_remaining > 0:
            self.artifact.extend(chunk[:artifact_remaining])


class LocalCommandRunner:
    """Execute authorized argument lists with bounded resources and sanitized results."""

    def __init__(
        self,
        *,
        executable_policy: ExecutablePolicy,
        path_policy: PathPolicy,
        environment_policy: EnvironmentPolicy,
        command_policy: CommandPolicy | None = None,
        redactor: Redactor | None = None,
        poll_interval_seconds: float = 0.01,
        termination_grace_seconds: float = 0.5,
    ) -> None:
        if not 0 < poll_interval_seconds <= 1:
            raise ValueError("poll interval must be positive and at most one second")
        if not 0 < termination_grace_seconds <= 10:
            raise ValueError("termination grace must be positive and at most ten seconds")
        self._executables = executable_policy
        self._paths = path_policy
        self._environment = environment_policy
        self._command_policy = command_policy or CommandPolicy()
        self._redactor = redactor or Redactor()
        self._poll_interval = poll_interval_seconds
        self._termination_grace = termination_grace_seconds

    def run(self, request: CommandRequest) -> CommandResult:
        """Return a normalized result; expected policy/process failures do not escape."""
        started_at = datetime.now(UTC)
        started_monotonic = time.monotonic()
        resolved_executable: Path | None = None
        try:
            self._command_policy.validate(request)
            resolved_executable = self._executables.resolve(request.executable)
            working_directory = self._paths.resolve_working_directory(request.working_directory)
            artifact_directory = (
                self._paths.resolve_artifact_directory(request.artifact_directory)
                if request.artifact_directory is not None
                else None
            )
            environment = self._environment.build(request.environment.to_mapping())
        except CommandPolicyError as error:
            return self._failure_result(
                request=request,
                status=CommandStatus.POLICY_REJECTED,
                category=error.category,
                message=str(error),
                resolved_executable=resolved_executable,
                started_at=started_at,
                started_monotonic=started_monotonic,
            )
        except Exception as error:
            return self._failure_result(
                request=request,
                status=CommandStatus.INTERNAL_ERROR,
                category=CommandFailureCategory.INTERNAL,
                message=f"internal command runner failure: {type(error).__name__}",
                resolved_executable=resolved_executable,
                started_at=started_at,
                started_monotonic=started_monotonic,
            )

        try:
            request_redactor = self._redactor.with_secrets(
                self._environment.sensitive_values(environment)
            )
        except ValueError:
            return self._failure_result(
                request=request,
                status=CommandStatus.POLICY_REJECTED,
                category=CommandFailureCategory.ENVIRONMENT,
                message="sensitive environment exceeds redaction policy bounds",
                resolved_executable=resolved_executable,
                started_at=started_at,
                started_monotonic=started_monotonic,
            )
        try:
            cancelled_before_launch = (
                request.cancellation is not None and request.cancellation.is_cancelled()
            )
        except Exception as error:
            return self._failure_result(
                request=request,
                status=CommandStatus.INTERNAL_ERROR,
                category=CommandFailureCategory.INTERNAL,
                message=f"internal command runner failure: {type(error).__name__}",
                resolved_executable=resolved_executable,
                started_at=started_at,
                started_monotonic=started_monotonic,
            )
        if cancelled_before_launch:
            return self._failure_result(
                request=request,
                status=CommandStatus.CANCELLED,
                category=CommandFailureCategory.CANCELLATION,
                message="command was cancelled before launch",
                resolved_executable=resolved_executable,
                started_at=started_at,
                started_monotonic=started_monotonic,
            )

        timeout_started = time.monotonic()
        try:
            process = self._launch(
                resolved_executable,
                request,
                working_directory=working_directory,
                environment=environment,
            )
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            return self._failure_result(
                request=request,
                status=CommandStatus.LAUNCH_FAILED,
                category=CommandFailureCategory.LAUNCH,
                message=f"process launch failed: {type(error).__name__}",
                resolved_executable=resolved_executable,
                started_at=started_at,
                started_monotonic=started_monotonic,
            )
        except Exception as error:
            return self._failure_result(
                request=request,
                status=CommandStatus.INTERNAL_ERROR,
                category=CommandFailureCategory.INTERNAL,
                message=f"internal command runner failure: {type(error).__name__}",
                resolved_executable=resolved_executable,
                started_at=started_at,
                started_monotonic=started_monotonic,
            )
        try:
            return self._complete_process(
                process=process,
                request=request,
                artifact_directory=artifact_directory,
                resolved_executable=resolved_executable,
                request_redactor=request_redactor,
                timeout_started=timeout_started,
                started_at=started_at,
                started_monotonic=started_monotonic,
            )
        except Exception as error:
            return self._failure_result(
                request=request,
                status=CommandStatus.INTERNAL_ERROR,
                category=CommandFailureCategory.INTERNAL,
                message=f"internal command runner failure: {type(error).__name__}",
                resolved_executable=resolved_executable,
                started_at=started_at,
                started_monotonic=started_monotonic,
            )

    def _complete_process(
        self,
        *,
        process: subprocess.Popen[bytes],
        request: CommandRequest,
        artifact_directory: Path | None,
        resolved_executable: Path,
        request_redactor: Redactor,
        timeout_started: float,
        started_at: datetime,
        started_monotonic: float,
    ) -> CommandResult:
        artifact_limit = (
            request.output_limits.artifact_bytes_per_stream if artifact_directory is not None else 0
        )
        stdout_collector = _StreamCollector(request.output_limits.stdout_bytes, artifact_limit)
        stderr_collector = _StreamCollector(request.output_limits.stderr_bytes, artifact_limit)
        stdout_thread: Thread | None = None
        stderr_thread: Thread | None = None
        stdin_thread: Thread | None = None
        terminal_status: CommandStatus | None = None
        terminal_category: CommandFailureCategory | None = None
        terminal_message: str | None = None
        try:
            stdout_thread = self._reader_thread(process.stdout, stdout_collector)
            stderr_thread = self._reader_thread(process.stderr, stderr_collector)
            stdin_thread = self._stdin_thread(process.stdin, request.stdin)
            while True:
                if process.poll() is not None:
                    break
                if request.cancellation is not None and request.cancellation.is_cancelled():
                    terminal_status = CommandStatus.CANCELLED
                    terminal_category = CommandFailureCategory.CANCELLATION
                    terminal_message = "command was cancelled during execution"
                    break
                if time.monotonic() - timeout_started >= request.timeout_seconds:
                    terminal_status = CommandStatus.TIMEOUT
                    terminal_category = CommandFailureCategory.TIMEOUT
                    terminal_message = "command exceeded its configured timeout"
                    break
                time.sleep(self._poll_interval)

            if terminal_status is not None:
                self._terminate(process)
            else:
                process.wait(timeout=self._termination_grace)
        finally:
            try:
                if process.poll() is None:
                    self._terminate(process)
            finally:
                self._finish_streams(process, stdout_thread, stderr_thread, stdin_thread)

        stdout, stdout_artifact_failed = self._finalize_stream(
            collector=stdout_collector,
            stream=OutputStream.STDOUT,
            request=request,
            artifact_directory=artifact_directory,
            redactor=request_redactor,
        )
        stderr, stderr_artifact_failed = self._finalize_stream(
            collector=stderr_collector,
            stream=OutputStream.STDERR,
            request=request,
            artifact_directory=artifact_directory,
            redactor=request_redactor,
        )
        exit_code = process.returncode
        failure: CommandFailure | None = None
        if terminal_status is not None:
            status = terminal_status
            assert terminal_category is not None
            assert terminal_message is not None
            failure = CommandFailure(terminal_category, terminal_message)
        elif stdout_artifact_failed or stderr_artifact_failed:
            status = CommandStatus.OUTPUT_ARTIFACT_FAILED
            failure = CommandFailure(
                CommandFailureCategory.OUTPUT_ARTIFACT,
                "redacted overflow artifact could not be written",
            )
        elif exit_code in request.expected_exit_codes:
            status = CommandStatus.SUCCESS
        else:
            status = CommandStatus.NONZERO_EXIT

        completed_at = datetime.now(UTC)
        return CommandResult(
            correlation_id=request.correlation_id,
            executable=request.executable,
            resolved_executable=resolved_executable,
            status=status,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=max(0.0, time.monotonic() - started_monotonic),
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            failure=failure,
        )

    def _launch(
        self,
        executable: Path,
        request: CommandRequest,
        *,
        working_directory: Path,
        environment: dict[str, str],
    ) -> subprocess.Popen[bytes]:
        command = [str(executable), *request.arguments]
        stdin = subprocess.PIPE if request.stdin is not None else subprocess.DEVNULL
        if os.name == "nt":
            return subprocess.Popen(
                command,
                cwd=str(working_directory),
                env=environment,
                stdin=stdin,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                text=False,
                bufsize=0,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        return subprocess.Popen(
            command,
            cwd=str(working_directory),
            env=environment,
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            text=False,
            bufsize=0,
            start_new_session=True,
        )

    def _reader_thread(
        self,
        stream: IO[bytes] | None,
        collector: _StreamCollector,
    ) -> Thread:
        def read() -> None:
            if stream is None:
                return
            try:
                while chunk := stream.read(_READ_SIZE):
                    collector.add(chunk)
            except (OSError, ValueError):
                return

        thread = Thread(target=read, name="revanent-command-output", daemon=True)
        thread.start()
        return thread

    def _stdin_thread(self, stream: IO[bytes] | None, data: bytes | None) -> Thread | None:
        if stream is None or data is None:
            return None

        def write() -> None:
            try:
                stream.write(data)
                stream.flush()
            except (BrokenPipeError, OSError, ValueError):
                pass
            finally:
                with suppress(OSError):
                    stream.close()

        thread = Thread(target=write, name="revanent-command-input", daemon=True)
        thread.start()
        return thread

    def _terminate(self, process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.kill(-process.pid, signal.SIGTERM)
            else:
                process.terminate()
        except (OSError, ProcessLookupError):
            pass
        try:
            process.wait(timeout=self._termination_grace)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            if os.name == "posix":
                os.kill(-process.pid, getattr(signal, "SIGKILL", signal.SIGTERM))
            else:
                process.kill()
        except (OSError, ProcessLookupError):
            pass
        try:
            process.wait(timeout=self._termination_grace)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=self._termination_grace)

    def _finish_streams(
        self,
        process: subprocess.Popen[bytes],
        stdout_thread: Thread | None,
        stderr_thread: Thread | None,
        stdin_thread: Thread | None,
    ) -> None:
        for thread in (stdout_thread, stderr_thread, stdin_thread):
            if thread is not None:
                thread.join(timeout=self._termination_grace)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                with suppress(OSError):
                    stream.close()
        for thread in (stdout_thread, stderr_thread, stdin_thread):
            if thread is not None and thread.is_alive():
                thread.join(timeout=self._termination_grace)

    def _finalize_stream(
        self,
        *,
        collector: _StreamCollector,
        stream: OutputStream,
        request: CommandRequest,
        artifact_directory: Path | None,
        redactor: Redactor,
    ) -> tuple[CapturedOutput, bool]:
        truncated = collector.observed > collector.capture_limit
        text = redactor.decode_and_redact(bytes(collector.captured), truncated=truncated)
        text, redaction_truncated = self._bounded_text(text, collector.capture_limit)
        if truncated:
            text += f"\n[revanent: {stream.value} truncated; observed {collector.observed} bytes]"
        if redaction_truncated:
            text += f"\n[revanent: redacted {stream.value} representation truncated]"

        reference: ArtifactReference | None = None
        artifact_failed = False
        if (truncated or redaction_truncated) and artifact_directory is not None:
            artifact_truncated = collector.observed > len(collector.artifact)
            artifact_text = redactor.decode_and_redact(
                bytes(collector.artifact), truncated=artifact_truncated
            )
            if artifact_truncated:
                artifact_text += (
                    f"\n[revanent: {stream.value} artifact truncated; "
                    f"observed {collector.observed} bytes]"
                )
            redacted_bytes_observed = len(artifact_text.encode("utf-8"))
            artifact_data = self._bounded_utf8(
                artifact_text,
                request.output_limits.artifact_bytes_per_stream,
            )
            artifact_representation_truncated = len(artifact_data) < redacted_bytes_observed
            try:
                target = self._paths.artifact_path(
                    artifact_directory,
                    f"{request.correlation_id}.{stream.value}.log",
                )
                self._atomic_write(target, artifact_data)
            except (OSError, CommandPolicyError):
                artifact_failed = True
            else:
                reference = ArtifactReference(
                    path=target,
                    stream=stream,
                    status=(
                        ArtifactStatus.TRUNCATED
                        if artifact_truncated or artifact_representation_truncated
                        else ArtifactStatus.COMPLETE
                    ),
                    observed_bytes=collector.observed,
                    source_bytes_retained=len(collector.artifact),
                    redacted_bytes_observed=redacted_bytes_observed,
                    stored_bytes=len(artifact_data),
                )
        return (
            CapturedOutput(
                text=text,
                observed_bytes=collector.observed,
                retained_bytes=len(collector.captured),
                truncated=truncated,
                redaction_truncated=redaction_truncated,
                artifact=reference,
            ),
            artifact_failed,
        )

    @staticmethod
    def _bounded_text(value: str, limit: int) -> tuple[str, bool]:
        encoded = value.encode("utf-8")
        if len(encoded) <= limit:
            return value, False
        return encoded[:limit].decode("utf-8", errors="ignore"), True

    @staticmethod
    def _bounded_utf8(value: str, limit: int) -> bytes:
        encoded = value.encode("utf-8")
        if len(encoded) <= limit:
            return encoded
        marker = b"\n[revanent: redacted artifact byte limit reached]"
        if len(marker) >= limit:
            return marker[:limit]
        prefix = encoded[: limit - len(marker)].decode("utf-8", errors="ignore").encode("utf-8")
        return prefix + marker

    @staticmethod
    def _atomic_write(target: Path, data: bytes) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
        )
        temporary = Path(temporary_name)
        try:
            with suppress(OSError):
                os.chmod(temporary, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        except BaseException:
            with suppress(OSError):
                os.close(descriptor)
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
            raise

    @staticmethod
    def _empty_output() -> CapturedOutput:
        return CapturedOutput(text="", observed_bytes=0, retained_bytes=0, truncated=False)

    def _failure_result(
        self,
        *,
        request: CommandRequest,
        status: CommandStatus,
        category: CommandFailureCategory,
        message: str,
        resolved_executable: Path | None,
        started_at: datetime,
        started_monotonic: float,
    ) -> CommandResult:
        completed_at = datetime.now(UTC)
        return CommandResult(
            correlation_id=request.correlation_id,
            executable=request.executable,
            resolved_executable=resolved_executable,
            status=status,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=max(0.0, time.monotonic() - started_monotonic),
            stdout=self._empty_output(),
            stderr=self._empty_output(),
            failure=CommandFailure(category, self._redactor.redact(message)),
        )
