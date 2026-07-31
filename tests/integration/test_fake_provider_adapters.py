from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from revanent.agents import (
    CodexRepairAdapter,
    CodexReviewerAdapter,
    OpenCodeBuilderAdapter,
    ProviderAdapterSettings,
    ProviderCompatibility,
    detect_codex,
    detect_opencode,
)
from revanent.commands import (
    CommandPolicy,
    EnvironmentPolicy,
    ExecutablePolicy,
    ExecutableRule,
    LocalCommandRunner,
    PathPolicy,
    Redactor,
)
from revanent.ports import (
    AgentRequest,
    AgentRole,
    AgentRouting,
    AgentStatus,
    EnvironmentOverrides,
    ProviderId,
    WorkspaceReference,
)
from tests.agent_factories import make_request

FAKE_PROVIDER = Path(__file__).parents[1] / "fixtures" / "fake_provider.py"


def _wrapper(tmp_path: Path, provider: str) -> Path:
    if os.name == "nt":
        wrapper = tmp_path / f"{provider}.cmd"
        wrapper.write_text(
            f'@echo off\r\n"{sys.executable}" "{FAKE_PROVIDER}" {provider} %*\r\n',
            encoding="utf-8",
        )
    else:
        wrapper = tmp_path / provider
        wrapper.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_PROVIDER}" {provider} "$@"\n',
            encoding="utf-8",
        )
        wrapper.chmod(0o700)
    return wrapper.resolve(strict=True)


def _runner(tmp_path: Path, worktree: Path, *, secret: str | None = None) -> LocalCommandRunner:
    codex = _wrapper(tmp_path, "codex")
    opencode = _wrapper(tmp_path, "opencode")
    baseline_names = (
        ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "PROMPT") if os.name == "nt" else ()
    )
    baseline = {name: os.environ[name] for name in baseline_names if name in os.environ}
    if os.name == "nt":
        baseline.setdefault("PROMPT", "$P$G")
    extension = (".cmd",) if os.name == "nt" else ()
    return LocalCommandRunner(
        executable_policy=ExecutablePolicy(
            (
                ExecutableRule("codex", (codex,), allowed_extensions=extension),
                ExecutableRule("opencode", (opencode,), allowed_extensions=extension),
            )
        ),
        path_policy=PathPolicy((worktree.resolve(strict=True),)),
        environment_policy=EnvironmentPolicy(
            baseline,
            allowed_override_keys=frozenset({"API_TOKEN", "FAKE_PROVIDER_RECORD"}),
            allowed_sensitive_keys=frozenset({"API_TOKEN"}),
        ),
        command_policy=CommandPolicy(allow_stdin=True),
        redactor=Redactor((secret,) if secret else ()),
        poll_interval_seconds=0.005,
        termination_grace_seconds=0.5,
    )


def _request(
    role: AgentRole, worktree: Path, *, environment_names: tuple[str, ...] = ()
) -> AgentRequest:
    request = make_request(role)
    return request.model_copy(
        update={
            "workspace": WorkspaceReference(
                kind=request.workspace.kind,
                reference_id="owned.fake-provider",
                root=worktree.resolve(strict=True),
            ),
            "routing": AgentRouting(
                provider_id=ProviderId("opencode" if role is AgentRole.BUILDER else "codex"),
                model="fixture-model",
            ),
            "allowed_environment_names": environment_names,
        }
    )


def test_fake_executables_detect_and_invoke_all_provider_roles(tmp_path: Path) -> None:
    worktree = tmp_path / "owned-worktree"
    worktree.mkdir()
    runner = _runner(tmp_path, worktree)
    open_detection = detect_opencode(runner, working_directory=worktree)
    codex_detection = detect_codex(runner, working_directory=worktree)

    builder = OpenCodeBuilderAdapter(runner, open_detection).invoke(
        _request(AgentRole.BUILDER, worktree)
    )
    reviewer = CodexReviewerAdapter(runner, codex_detection).invoke(
        _request(AgentRole.REVIEWER, worktree)
    )
    repairer = CodexRepairAdapter(runner, codex_detection, write_authorized=True).invoke(
        _request(AgentRole.REPAIRER, worktree)
    )

    assert open_detection.compatibility is ProviderCompatibility.AVAILABLE
    assert codex_detection.compatibility is ProviderCompatibility.AVAILABLE
    assert [builder.status, reviewer.status, repairer.status] == [
        AgentStatus.COMPLETED,
        AgentStatus.COMPLETED,
        AgentStatus.COMPLETED,
    ]
    assert not (worktree / "unexpected-review-write.txt").exists()
    assert (worktree / "repair-mode-seen.txt").read_text(encoding="utf-8") == (
        "fake repair evidence"
    )


def test_fake_reviewer_receives_read_only_mode_and_minimal_environment(
    tmp_path: Path,
) -> None:
    worktree = tmp_path / "owned-worktree"
    records = tmp_path / "records"
    worktree.mkdir()
    records.mkdir()
    record = records / "review.json"
    runner = _runner(tmp_path, worktree)
    detection = detect_codex(runner, working_directory=worktree)
    settings = ProviderAdapterSettings(
        environment=EnvironmentOverrides.from_mapping({"FAKE_PROVIDER_RECORD": str(record)})
    )

    response = CodexReviewerAdapter(runner, detection, settings=settings).invoke(
        _request(
            AgentRole.REVIEWER,
            worktree,
            environment_names=("FAKE_PROVIDER_RECORD",),
        )
    )
    received = json.loads(record.read_text(encoding="utf-8"))

    assert response.status is AgentStatus.COMPLETED
    assert received["sandbox"] == "read-only"
    assert "workspace-write" not in received["arguments"]
    expected_baseline = (
        {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT"} & set(os.environ)
        if os.name == "nt"
        else set()
    )
    if os.name == "nt":
        expected_baseline.add("PROMPT")
    assert set(received["environment_keys"]) == expected_baseline | {"FAKE_PROVIDER_RECORD"}
    assert Path(received["cwd"]) == worktree.resolve()
    assert not (worktree / "unexpected-review-write.txt").exists()


def test_fake_provider_secret_echo_is_redacted_before_agent_parsing(tmp_path: Path) -> None:
    worktree = tmp_path / "owned-worktree"
    worktree.mkdir()
    secret = "fixture-sensitive-value"
    runner = _runner(tmp_path, worktree, secret=secret)
    detection = detect_codex(runner, working_directory=worktree)
    settings = ProviderAdapterSettings(
        environment=EnvironmentOverrides.from_mapping({"API_TOKEN": secret}),
        sensitive_values=(secret,),
    )

    response = CodexReviewerAdapter(runner, detection, settings=settings).invoke(
        _request(AgentRole.REVIEWER, worktree, environment_names=("API_TOKEN",))
    )

    assert response.status is AgentStatus.COMPLETED
    assert response.public_text == "[REDACTED]"
    assert secret not in response.model_dump_json()
