from __future__ import annotations

import ctypes
import os
import struct
from ctypes import wintypes
from pathlib import Path

import pytest

from revanent.commands import (
    CommandPolicy,
    EnvironmentPolicy,
    ExecutablePolicy,
    ExecutableRule,
    PathPolicy,
)
from revanent.ports import (
    CommandPolicyError,
    CommandRequest,
    EnvironmentPolicyError,
    ExecutablePolicyError,
    ExecutableUnavailableError,
    OutputArtifactPolicyError,
    OutputLimits,
    WorkingDirectoryPolicyError,
)


def _create_windows_junction(link: Path, target: Path) -> None:
    link.mkdir()
    substitute = f"\\??\\{target.resolve(strict=True)}".encode("utf-16-le")
    print_name = str(target.resolve(strict=True)).encode("utf-16-le")
    path_buffer = substitute + b"\x00\x00" + print_name + b"\x00\x00"
    data = (
        struct.pack(
            "<LHHHHHH",
            0xA0000003,
            8 + len(path_buffer),
            0,
            0,
            len(substitute),
            len(substitute) + 2,
            len(print_name),
        )
        + path_buffer
    )
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.DeviceIoControl.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    )
    kernel32.DeviceIoControl.restype = wintypes.BOOL
    handle = kernel32.CreateFileW(
        str(link),
        0x40000000,
        0,
        None,
        3,
        0x00200000 | 0x02000000,
        None,
    )
    if handle == wintypes.HANDLE(-1).value:
        raise OSError(ctypes.get_last_error(), "cannot open junction directory")
    try:
        returned = wintypes.DWORD()
        buffer = ctypes.create_string_buffer(data)
        if not kernel32.DeviceIoControl(
            handle,
            0x000900A4,
            buffer,
            len(data),
            None,
            0,
            ctypes.byref(returned),
            None,
        ):
            raise OSError(ctypes.get_last_error(), "cannot create directory junction")
    finally:
        kernel32.CloseHandle(handle)


def test_executable_policy_uses_explicit_candidate_order(tmp_path: Path) -> None:
    first = tmp_path / ("first.exe" if os.name == "nt" else "first")
    second = tmp_path / ("second.exe" if os.name == "nt" else "second")
    first.write_text("first", encoding="ascii")
    second.write_text("second", encoding="ascii")
    if os.name != "nt":
        first.chmod(0o700)
        second.chmod(0o700)
    extensions = (".exe",) if os.name == "nt" else ()
    policy = ExecutablePolicy(
        (ExecutableRule("tool", (first, second), allowed_extensions=extensions),)
    )

    assert policy.resolve("tool") == first.resolve(strict=True)


def test_command_policy_applies_runner_specific_resource_and_stdin_ceilings(
    tmp_path: Path,
) -> None:
    policy = CommandPolicy(
        max_timeout_seconds=10,
        max_stdout_bytes=100,
        max_stderr_bytes=100,
        max_artifact_bytes_per_stream=100,
        max_stdin_bytes=10,
        allow_stdin=False,
        allow_artifacts=False,
    )
    request = CommandRequest(
        executable="tool",
        arguments=(),
        working_directory=tmp_path,
        correlation_id="bounded-policy",
        timeout_seconds=5,
        output_limits=OutputLimits(
            stdout_bytes=100,
            stderr_bytes=100,
            artifact_bytes_per_stream=100,
        ),
    )

    policy.validate(request)
    with pytest.raises(CommandPolicyError, match="timeout"):
        policy.validate(
            CommandRequest(
                executable="tool",
                arguments=(),
                working_directory=tmp_path,
                correlation_id="timeout-policy",
                timeout_seconds=11,
                output_limits=request.output_limits,
            )
        )
    with pytest.raises(CommandPolicyError, match="standard input"):
        policy.validate(
            CommandRequest(
                executable="tool",
                arguments=(),
                working_directory=tmp_path,
                correlation_id="stdin-policy",
                timeout_seconds=5,
                output_limits=request.output_limits,
                stdin=b"data",
            )
        )


@pytest.mark.parametrize(
    "bypass",
    ["../tool", "subdir/tool", "subdir\\tool", "/absolute/tool", "C:\\tool.exe"],
)
def test_executable_path_bypass_attempts_are_rejected(tmp_path: Path, bypass: str) -> None:
    candidate = tmp_path / ("tool.exe" if os.name == "nt" else "tool")
    candidate.write_text("tool", encoding="ascii")
    if os.name != "nt":
        candidate.chmod(0o700)
    extensions = (".exe",) if os.name == "nt" else ()
    policy = ExecutablePolicy(
        (ExecutableRule("tool", (candidate,), allowed_extensions=extensions),)
    )

    with pytest.raises(ExecutablePolicyError):
        policy.resolve(bypass)


def test_disallowed_and_unavailable_executables_are_distinct(tmp_path: Path) -> None:
    missing = tmp_path / ("missing.exe" if os.name == "nt" else "missing")
    extensions = (".exe",) if os.name == "nt" else ()
    policy = ExecutablePolicy(
        (ExecutableRule("allowed", (missing,), allowed_extensions=extensions),)
    )

    with pytest.raises(ExecutablePolicyError, match="not authorized"):
        policy.resolve("other")
    with pytest.raises(ExecutableUnavailableError, match="unavailable"):
        policy.resolve("allowed")


def test_search_path_ignores_empty_relative_and_unc_entries(tmp_path: Path) -> None:
    separator = ";" if os.name == "nt" else ":"
    search = separator.join(("", ".", r"\\server\share\tools", str(tmp_path)))
    extensions = (".exe",) if os.name == "nt" else ()
    policy = ExecutablePolicy.from_search_path(
        ("tool",),
        search,
        windows=os.name == "nt",
        windows_extensions=extensions,
    )

    assert policy.rules[0].candidates == (
        (tmp_path / "tool.exe",) if os.name == "nt" else (tmp_path / "tool",)
    )


def test_search_path_excludes_repository_local_candidates(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    external = tmp_path / "external"
    repository.mkdir()
    external.mkdir()
    separator = ";" if os.name == "nt" else ":"
    extensions = (".exe",) if os.name == "nt" else ()

    policy = ExecutablePolicy.from_search_path(
        ("tool",),
        separator.join((str(repository), str(external))),
        windows=os.name == "nt",
        windows_extensions=extensions,
        excluded_roots=(repository.resolve(),),
    )

    assert policy.rules[0].candidates == (
        (external / "tool.exe",) if os.name == "nt" else (external / "tool",)
    )


def test_working_directory_and_relative_paths_are_resolved_inside_root(tmp_path: Path) -> None:
    root = tmp_path / "approved root"
    nested = root / "nested"
    nested.mkdir(parents=True)
    policy = PathPolicy((root.resolve(),))

    assert policy.resolve_working_directory(nested.resolve()) == nested.resolve()
    assert (
        policy.resolve_relative(root.resolve(), Path("new/file.txt"), must_exist=False)
        == (root / "new" / "file.txt").resolve()
    )


def test_path_policy_rejects_traversal_absolute_and_sibling_prefix_attacks(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    sibling = tmp_path / "repository-evil"
    root.mkdir()
    sibling.mkdir()
    policy = PathPolicy((root.resolve(),))

    with pytest.raises(WorkingDirectoryPolicyError, match="outside"):
        policy.resolve_working_directory(sibling.resolve())
    with pytest.raises(WorkingDirectoryPolicyError, match="parent traversal"):
        policy.resolve_relative(root.resolve(), Path("../repository-evil"), must_exist=True)
    with pytest.raises(WorkingDirectoryPolicyError, match="relative"):
        policy.resolve_relative(root.resolve(), sibling.resolve(), must_exist=True)
    with pytest.raises(WorkingDirectoryPolicyError, match="relative"):
        policy.resolve_relative(root.resolve(), Path("C:/outside"), must_exist=False)


def test_symlink_escape_is_rejected_where_supported(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    link = root / "escape"
    if os.name == "nt":
        _create_windows_junction(link, outside)
    else:
        link.symlink_to(outside, target_is_directory=True)
    policy = PathPolicy((root.resolve(),))

    with pytest.raises(WorkingDirectoryPolicyError, match="escapes"):
        policy.resolve_relative(root.resolve(), Path("escape/file.txt"), must_exist=False)


@pytest.mark.skipif(os.name != "nt", reason="Windows case behavior")
def test_windows_drive_and_case_normalization_preserves_containment(tmp_path: Path) -> None:
    root = tmp_path / "CaseRoot"
    root.mkdir()
    rendered = str(root.resolve())
    alternate_case = Path(rendered.swapcase())
    policy = PathPolicy((root.resolve(),))

    assert policy.resolve_working_directory(alternate_case) == root.resolve()


def test_unc_roots_are_rejected_without_explicit_authorization() -> None:
    with pytest.raises(ValueError, match="UNC"):
        PathPolicy((Path(r"\\server\share\repo"),))


def test_artifacts_require_separate_contained_roots(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    artifacts = root / ".revanent" / "runs" / "run-1"
    outside = tmp_path / "outside"
    artifacts.mkdir(parents=True)
    outside.mkdir()
    policy = PathPolicy((root.resolve(),), artifact_roots=(artifacts.resolve(),))

    assert policy.resolve_artifact_directory(artifacts.resolve()) == artifacts.resolve()
    with pytest.raises(OutputArtifactPolicyError, match="outside"):
        policy.resolve_artifact_directory(outside.resolve())
    with pytest.raises(OutputArtifactPolicyError, match="filename"):
        policy.artifact_path(artifacts.resolve(), "../escape.log")


def test_environment_policy_filters_host_data_and_applies_explicit_precedence() -> None:
    policy = EnvironmentPolicy(
        {"PATH": "baseline", "LANG": "C"},
        allowed_override_keys=frozenset({"LANG", "SAFE"}),
        forbidden_keys=frozenset({"FORBIDDEN"}),
        windows=False,
    )

    child = policy.build({"LANG": "C.UTF-8", "SAFE": "value"})

    assert child == {"PATH": "baseline", "LANG": "C.UTF-8", "SAFE": "value"}
    assert "UNRELATED_HOST_VALUE" not in child


def test_environment_policy_rejects_forbidden_unknown_and_sensitive_keys() -> None:
    policy = EnvironmentPolicy(
        {},
        allowed_override_keys=frozenset({"SAFE", "API_TOKEN"}),
        forbidden_keys=frozenset({"FORBIDDEN"}),
        windows=False,
    )

    with pytest.raises(EnvironmentPolicyError, match="forbidden"):
        policy.build({"FORBIDDEN": "value"})
    with pytest.raises(EnvironmentPolicyError, match="not authorized"):
        policy.build({"UNKNOWN": "value"})
    with pytest.raises(EnvironmentPolicyError, match="sensitive"):
        policy.build({"API_TOKEN": "secret"})


def test_explicit_sensitive_environment_values_are_reported_for_redaction() -> None:
    policy = EnvironmentPolicy(
        {},
        allowed_override_keys=frozenset({"API_TOKEN"}),
        allowed_sensitive_keys=frozenset({"API_TOKEN"}),
        windows=False,
    )
    child = policy.build({"API_TOKEN": "top-secret"})

    assert child == {"API_TOKEN": "top-secret"}
    assert policy.sensitive_values(child) == ("top-secret",)
    assert "top-secret" not in repr(policy)


def test_windows_environment_keys_collide_case_insensitively() -> None:
    with pytest.raises(ValueError, match="collide"):
        EnvironmentPolicy({"Path": "one", "PATH": "two"}, windows=True)

    policy = EnvironmentPolicy(
        {"Path": "baseline"},
        allowed_override_keys=frozenset({"safe"}),
        windows=True,
    )
    assert policy.build({"SaFe": "value"}) == {"PATH": "baseline", "SAFE": "value"}
