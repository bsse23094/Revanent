"""Typed deterministic factories shared by P3-001 tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from revanent.domain import (
    AgentAttemptId,
    AgentInvocationId,
    ReviewResult,
    ReviewVerdict,
    RunId,
    WorkPackageId,
)
from revanent.ports.agents import (
    AdapterId,
    AgentArtifactPolicy,
    AgentAvailability,
    AgentCapabilities,
    AgentProviderIdentity,
    AgentRequest,
    AgentResponse,
    AgentRole,
    AgentStatus,
    BuilderPayload,
    ExpectedAgentCapabilities,
    ProviderId,
    RepairerPayload,
    RepositoryPath,
    ReviewerPayload,
    ScopePath,
    StructuredParseStatus,
    WorkspaceKind,
    WorkspaceReference,
)

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)


def make_capabilities(
    *,
    roles: tuple[AgentRole, ...] = (
        AgentRole.BUILDER,
        AgentRole.REPAIRER,
        AgentRole.REVIEWER,
    ),
    available: AgentAvailability = AgentAvailability.AVAILABLE,
    reason: str | None = None,
    writes: bool = True,
    cancellation: bool = True,
    artifacts: bool = True,
) -> AgentCapabilities:
    return AgentCapabilities(
        provider_id=ProviderId("fake"),
        adapter_id=AdapterId("fake.agent"),
        adapter_version="1.0.0",
        supported_roles=roles,
        supports_structured_output=True,
        supports_read_only=True,
        supports_repository_writes=writes,
        supports_cancellation=cancellation,
        supports_timeout=True,
        supports_usage_reporting=True,
        supports_artifact_references=artifacts,
        supports_repair=AgentRole.REPAIRER in roles,
        availability=available,
        reason=reason,
        detected_model="deterministic-fixture",
    )


def make_request(
    role: AgentRole = AgentRole.BUILDER,
    *,
    timeout_seconds: int = 30,
    cancellation: bool = False,
    artifacts: bool = False,
    invocation_hex: str = "1" * 32,
) -> AgentRequest:
    is_reviewer = role is AgentRole.REVIEWER
    is_repairer = role is AgentRole.REPAIRER
    return AgentRequest(
        invocation_id=AgentInvocationId(f"inv_{invocation_hex}"),
        run_id=RunId(f"run_{'2' * 32}"),
        work_package_id=WorkPackageId("P3-001"),
        attempt_id=AgentAttemptId(f"attempt_{'3' * 32}"),
        attempt_number=1,
        role=role,
        objective="Produce bounded deterministic evidence.",
        allowed_scope=(ScopePath("src/**"), ScopePath("tests/**")),
        forbidden_scope=(ScopePath(".env"), ScopePath(".git/**")),
        workspace=WorkspaceReference(
            kind=WorkspaceKind.WORKTREE,
            reference_id="worktree.fixture",
            root=Path.cwd().resolve(),
        ),
        timeout_seconds=timeout_seconds,
        cancellation_reference="cancel.fixture" if cancellation else None,
        artifact_policy=AgentArtifactPolicy(
            artifact_root_id="run-artifacts.fixture",
            allow_artifact_references=artifacts,
            allow_raw_output_reference=artifacts,
            require_redaction=True,
        ),
        allowed_environment_names=("LANG", "PATH"),
        expected_capabilities=ExpectedAgentCapabilities(
            requires_read_only=is_reviewer,
            requires_repository_writes=not is_reviewer,
            requires_cancellation=cancellation,
            requires_usage_reporting=False,
            requires_artifact_references=artifacts,
            requires_repair=is_repairer,
        ),
    )


def make_identity() -> AgentProviderIdentity:
    return AgentProviderIdentity(
        provider_id=ProviderId("fake"),
        adapter_id=AdapterId("fake.agent"),
        adapter_version="1.0.0",
        model="deterministic-fixture",
    )


def make_payload(
    role: AgentRole = AgentRole.BUILDER,
) -> BuilderPayload | ReviewerPayload | RepairerPayload:
    if role is AgentRole.BUILDER:
        return BuilderPayload(
            implementation_summary="Implemented the bounded change.",
            files_inspected=(RepositoryPath("src/revanent/domain/models.py"),),
            files_claimed_changed=(RepositoryPath("src/revanent/ports/agents.py"),),
        )
    if role is AgentRole.REVIEWER:
        return ReviewerPayload(
            review=ReviewResult(
                verdict=ReviewVerdict.APPROVED,
                summary="Provider claims the reviewed evidence is acceptable.",
            ),
            files_inspected=(RepositoryPath("src/revanent/ports/agents.py"),),
        )
    return RepairerPayload(
        repair_summary="Applied the authorized bounded repair.",
        files_inspected=(RepositoryPath("src/revanent/ports/agents.py"),),
        files_claimed_changed=(RepositoryPath("src/revanent/ports/agents.py"),),
    )


def make_response(role: AgentRole = AgentRole.BUILDER) -> AgentResponse:
    request = make_request(role)
    return AgentResponse(
        invocation_id=request.invocation_id,
        run_id=request.run_id,
        work_package_id=request.work_package_id,
        attempt_id=request.attempt_id,
        attempt_number=request.attempt_number,
        role=request.role,
        status=AgentStatus.COMPLETED,
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=1),
        duration_ms=1_000,
        summary="Scripted completion",
        public_text="Bounded public evidence.",
        structured_parse_status=StructuredParseStatus.PARSED,
        payload=make_payload(role),
        identity=make_identity(),
    )
