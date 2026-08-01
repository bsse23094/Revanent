from __future__ import annotations

import hashlib
import json
import platform
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from revanent.agents import (
    CodexRepairAdapter,
    CodexReviewerAdapter,
    OpenCodeBuilderAdapter,
    ProviderCompatibility,
    detect_codex,
    detect_opencode,
)
from revanent.application.runtime import controlled_host_runner
from revanent.certification import (
    LiveCertificationAuthorization,
    LiveCertificationEvidence,
    LiveCertificationRole,
)
from revanent.domain import AgentAttemptId, AgentInvocationId, RunId, WorkPackageId
from revanent.ports import (
    AgentArtifactPolicy,
    AgentRequest,
    AgentRole,
    AgentRouting,
    AgentStatus,
    CommandRequest,
    CommandRunner,
    CommandStatus,
    ExpectedAgentCapabilities,
    ProviderId,
    ScopePath,
    WorkspaceKind,
    WorkspaceReference,
)

pytestmark = [pytest.mark.live, pytest.mark.network, pytest.mark.costed]


def _git(runner: CommandRunner, root: Path, *arguments: str) -> None:
    correlation = hashlib.sha256(" ".join(arguments).encode()).hexdigest()[:16]
    result = runner.run(
        CommandRequest(
            executable="git",
            arguments=arguments,
            working_directory=root,
            correlation_id="live-git-" + correlation,
            timeout_seconds=30,
        )
    )
    assert result.status is CommandStatus.SUCCESS


def _disposable_worktree(tmp_path: Path) -> tuple[Path, Path, CommandRunner]:
    source_checkout = Path.cwd().resolve()
    repository = (tmp_path / "live-source").resolve()
    worktree = (tmp_path / "live-owned-worktree").resolve()
    repository.mkdir()
    assert not repository.is_relative_to(source_checkout)
    assert not source_checkout.is_relative_to(repository)
    runner = controlled_host_runner(
        repository,
        ("git", "opencode", "codex", "python"),
        allow_provider_stdin=True,
    )
    _git(runner, repository, "init", "--initial-branch=main")
    (repository / "calculator.py").write_text(
        "def add(left: int, right: int) -> int:\n    return left + right\n",
        encoding="utf-8",
    )
    _git(runner, repository, "add", "calculator.py")
    _git(
        runner,
        repository,
        "-c",
        "user.name=Revanent Live Fixture",
        "-c",
        "user.email=revanent-live@example.invalid",
        "commit",
        "-m",
        "fixture baseline",
    )
    _git(runner, repository, "worktree", "add", "-b", "revanent/live-certification", str(worktree))
    return repository, worktree, runner


def _request(
    role: AgentRole,
    worktree: Path,
    authorization: LiveCertificationAuthorization,
    objective: str,
) -> AgentRequest:
    return AgentRequest(
        invocation_id=AgentInvocationId(f"inv_{'7' * 32}"),
        run_id=RunId(f"run_{'8' * 32}"),
        work_package_id=WorkPackageId("P7-001"),
        attempt_id=AgentAttemptId(f"attempt_{'9' * 32}"),
        attempt_number=1,
        role=role,
        objective=objective,
        allowed_scope=(ScopePath("calculator.py"),),
        forbidden_scope=(ScopePath(".env"), ScopePath(".git/**")),
        workspace=WorkspaceReference(
            kind=WorkspaceKind.WORKTREE,
            reference_id="live.owned-worktree",
            root=worktree,
        ),
        timeout_seconds=authorization.timeout_seconds,
        artifact_policy=AgentArtifactPolicy(
            artifact_root_id="live-certification",
            allow_artifact_references=False,
            allow_raw_output_reference=False,
            require_redaction=True,
        ),
        routing=AgentRouting(
            provider_id=ProviderId("opencode" if role is AgentRole.BUILDER else "codex"),
            model=authorization.model,
        ),
        expected_capabilities=ExpectedAgentCapabilities(
            requires_read_only=role is AgentRole.REVIEWER,
            requires_repository_writes=role is not AgentRole.REVIEWER,
            requires_repair=role is AgentRole.REPAIRER,
        ),
    )


def _source_digest(repository: Path) -> str:
    return hashlib.sha256((repository / "calculator.py").read_bytes()).hexdigest()


def _write_evidence(
    tmp_path: Path,
    authorization: LiveCertificationAuthorization,
    version: str,
    *,
    review_status: str,
    repair_status: str,
) -> None:
    evidence = LiveCertificationEvidence(
        scenario_id=f"live_{authorization.role.value.casefold()}",
        role=authorization.role,
        provider=authorization.provider,
        model=authorization.model,
        provider_version=version,
        platform=platform.platform(),
        python_version=sys.version.split()[0],
        generated_at=datetime.now(UTC),
        invocation_count=1,
        validation_status="PASSED",
        review_status=review_status,
        repair_status=repair_status,
        telemetry_provenance="UNAVAILABLE",
        repository_id="disposable-live-fixture",
        worktree_id="live.owned-worktree",
        limitations=("direct_adapter_certification", "provider_usage_unavailable"),
    )
    value = (
        json.dumps(
            evidence.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
    (tmp_path / f"{evidence.scenario_id}.json").write_bytes(value)
    assert b"authorization" not in value.lower()
    assert b"prompt" not in value.lower()


@pytest.mark.live_opencode
def test_live_opencode_builder_in_owned_disposable_worktree(
    tmp_path: Path,
    live_authorizer: Callable[[LiveCertificationRole], LiveCertificationAuthorization],
) -> None:
    authorization = live_authorizer(LiveCertificationRole.OPENCODE_BUILDER)
    repository, worktree, runner = _disposable_worktree(tmp_path)
    source_before = _source_digest(repository)
    detection = detect_opencode(runner, working_directory=worktree)
    assert detection.compatibility is ProviderCompatibility.AVAILABLE, detection.reason
    response = OpenCodeBuilderAdapter(runner, detection).invoke(
        _request(
            AgentRole.BUILDER,
            worktree,
            authorization,
            "Add a subtract function to calculator.py only and return the required envelope.",
        )
    )
    assert response.status is AgentStatus.COMPLETED, (
        response.failure.code if response.failure is not None else "unexpected_status"
    )
    assert _source_digest(repository) == source_before
    _write_evidence(
        tmp_path,
        authorization,
        detection.version or "unavailable",
        review_status="NOT_RUN",
        repair_status="NOT_RUN",
    )


@pytest.mark.live_codex
def test_live_codex_reviewer_is_read_only_in_owned_disposable_worktree(
    tmp_path: Path,
    live_authorizer: Callable[[LiveCertificationRole], LiveCertificationAuthorization],
) -> None:
    authorization = live_authorizer(LiveCertificationRole.CODEX_REVIEWER)
    repository, worktree, runner = _disposable_worktree(tmp_path)
    source_before = _source_digest(repository)
    worktree_before = _source_digest(worktree)
    detection = detect_codex(runner, working_directory=worktree)
    assert detection.compatibility is ProviderCompatibility.AVAILABLE, detection.reason
    response = CodexReviewerAdapter(runner, detection).invoke(
        _request(
            AgentRole.REVIEWER,
            worktree,
            authorization,
            "Review calculator.py read-only and return the required structured envelope.",
        )
    )
    assert response.status is AgentStatus.COMPLETED, (
        response.failure.code if response.failure is not None else "unexpected_status"
    )
    assert _source_digest(repository) == source_before
    assert _source_digest(worktree) == worktree_before
    _write_evidence(
        tmp_path,
        authorization,
        detection.version or "unavailable",
        review_status="COMPLETED",
        repair_status="NOT_RUN",
    )


@pytest.mark.live_codex
def test_live_codex_repair_is_explicit_and_validated_in_owned_worktree(
    tmp_path: Path,
    live_authorizer: Callable[[LiveCertificationRole], LiveCertificationAuthorization],
) -> None:
    authorization = live_authorizer(LiveCertificationRole.CODEX_REPAIRER)
    repository, worktree, runner = _disposable_worktree(tmp_path)
    source_before = _source_digest(repository)
    (worktree / "calculator.py").write_text(
        "def add(left: int, right: int) -> int:\n    return left - right\n",
        encoding="utf-8",
    )
    detection = detect_codex(runner, working_directory=worktree)
    assert detection.repair_surface_verified, detection.reason
    response = CodexRepairAdapter(
        runner,
        detection,
        write_authorized=authorization.write_authorized,
    ).invoke(
        _request(
            AgentRole.REPAIRER,
            worktree,
            authorization,
            "Repair calculator.add so add(2, 3) equals 5; change calculator.py only.",
        )
    )
    assert response.status is AgentStatus.COMPLETED, (
        response.failure.code if response.failure is not None else "unexpected_status"
    )
    namespace: dict[str, object] = {}
    exec((worktree / "calculator.py").read_text(encoding="utf-8"), namespace)
    assert namespace["add"](2, 3) == 5  # type: ignore[operator]
    assert _source_digest(repository) == source_before
    _write_evidence(
        tmp_path,
        authorization,
        detection.version or "unavailable",
        review_status="NOT_RUN",
        repair_status="COMPLETED",
    )
