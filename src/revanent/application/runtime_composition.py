"""Explicit local runtime composition for P6 workflow commands."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from revanent.agents import (
    CodexRepairAdapter,
    CodexReviewerAdapter,
    OpenCodeBuilderAdapter,
    ProviderAdapterSettings,
    ProviderCompatibility,
    detect_codex,
    detect_opencode,
)
from revanent.application.report_command import (
    ReportCommandComposition,
    ReportCommandService,
)
from revanent.application.reports import EvidenceReportService, ReportComposition
from revanent.application.runtime import _inspection_roots, controlled_host_runner
from revanent.application.workflows import RuntimeComposition, StartRunRequest, StatusComposition
from revanent.commands import PathPolicy
from revanent.config import EffectiveConfiguration, resolve_project_paths
from revanent.context import ContextDiscoveryInput, ContextSelectionRequest, ContextSelector
from revanent.domain import AgentAttemptId, AgentInvocationId, BudgetLimits, Run, RunId
from revanent.git import LocalGitRepository, WorktreeOwnershipStore
from revanent.orchestration import OrchestrationAdapters, OrchestrationService
from revanent.ports.agents import (
    AgentArtifactPolicy,
    AgentRequest,
    AgentRole,
    AgentRouting,
    ExpectedAgentCapabilities,
    ScopePath,
    WorkspaceKind,
    WorkspaceReference,
)
from revanent.ports.git import RepositoryIdentity, WorktreeCreationRequest, WorktreeId
from revanent.ports.orchestration import OrchestrationRequest
from revanent.ports.runtime import RuntimeBinding
from revanent.ports.validation import (
    ValidationArtifactPolicy,
    ValidationCommand,
    ValidationCommandClass,
    ValidationCommandId,
    ValidationExecutionPolicy,
    ValidationPlan,
    ValidationPlanId,
)
from revanent.reporting import LocalReportArtifactWriter, ReportRenderer
from revanent.review import LocalApprovalEvidence, ReviewGate
from revanent.storage import SQLiteRunRepository
from revanent.telemetry import TelemetryService
from revanent.validation import ValidationRunner


class _Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class RuntimeDependencyError(Exception):
    """A required configured provider capability is unavailable before execution."""


class _LocalEvidence:
    """Keep approval evidence local; provider output never supplies these facts."""

    def collect(self, *, validation_plan, validation_result, reviewer_response, observed_at):  # type: ignore[no-untyped-def]
        return LocalApprovalEvidence(
            run_id=validation_plan.run_id,
            work_package_id=validation_plan.work_package_id,
            validation_plan_id=validation_plan.id,
            review_invocation_id=reviewer_response.invocation_id,
            review_adapter_id=reviewer_response.identity.adapter_id,
            observed_at=observed_at,
            scope_justified=False,
            generated_files_consistent=False,
            lockfiles_consistent=False,
            evidence_complete=False,
            required_artifacts_complete=False,
            repository_clean=False,
            review_read_only_verified=True,
            side_effects_reconciled=True,
        )


def compose_runtime(
    effective: EffectiveConfiguration, *, require_providers: bool = True
) -> RuntimeComposition:
    """Wire only approved local adapters from one already validated project config."""
    paths = resolve_project_paths(effective)
    config = effective.config
    executable_names = tuple(
        dict.fromkeys(
            ("git", "opencode", "codex", *(item.command[0] for item in config.validation.commands))
        )
    )
    live_authorized = (
        require_providers
        and config.policy.allow_network
        and config.policy.allow_live_opencode_builder
        and config.policy.allow_live_codex_reviewer
    )
    runner = controlled_host_runner(
        effective.repository_root,
        executable_names,
        allow_provider_stdin=live_authorized,
    )
    git = LocalGitRepository(
        runner=runner,
        path_policy=PathPolicy(_inspection_roots(effective.repository_root)),
        worktree_root=paths.workspace_root,
        ownership_store=WorktreeOwnershipStore(paths.state_root),
    )
    storage = SQLiteRunRepository(paths.state_root / "runs.sqlite")
    storage.initialize()
    opencode = detect_opencode(runner, working_directory=effective.repository_root)
    codex = detect_codex(runner, working_directory=effective.repository_root)
    if require_providers and not config.policy.allow_network:
        raise RuntimeDependencyError("live provider network execution is not authorized")
    if require_providers and not config.policy.allow_live_opencode_builder:
        raise RuntimeDependencyError("live OpenCode builder execution is not authorized")
    if require_providers and not config.policy.allow_live_codex_reviewer:
        raise RuntimeDependencyError("live Codex reviewer execution is not authorized")
    if require_providers and opencode.compatibility is not ProviderCompatibility.AVAILABLE:
        raise RuntimeDependencyError("required builder capability is unavailable")
    if require_providers and codex.compatibility is not ProviderCompatibility.AVAILABLE:
        raise RuntimeDependencyError("required reviewer capability is unavailable")
    if (
        require_providers
        and config.policy.allow_codex_write_repair
        and not codex.repair_surface_verified
    ):
        raise RuntimeDependencyError("configured repair capability is unavailable")
    settings = ProviderAdapterSettings(artifact_directory=paths.report_root)
    builder = OpenCodeBuilderAdapter(runner, opencode, settings=settings)
    reviewer = CodexReviewerAdapter(runner, codex, settings=settings)
    repair = CodexRepairAdapter(
        runner, codex, write_authorized=config.policy.allow_codex_write_repair, settings=settings
    )
    telemetry = TelemetryService(storage)
    service = OrchestrationService(
        runs=storage,
        journal=storage,
        git=git,
        adapters=OrchestrationAdapters(builder, reviewer, builder, repair),
        validation=ValidationRunner(runner),
        review_gate=ReviewGate(),
        local_evidence=_LocalEvidence(),
        context_selector=ContextSelector(),
        telemetry=telemetry,
        clock=_Clock(),
    )

    def make_run(request: StartRunRequest) -> Run:
        now = datetime.now(UTC)
        return Run(
            id=RunId(f"run_{uuid4().hex}"),
            task=request.task,
            work_package=request.work_package,
            budgets=BudgetLimits(
                max_duration_seconds=config.budgets.max_total_minutes * 60,
                max_build_attempts=config.builder.max_attempts,
                max_review_attempts=config.reviewer.max_reviews,
                max_repair_attempts=config.reviewer.max_repairs,
                max_remote_tokens=config.budgets.max_remote_tokens,
                max_estimated_cost_usd=config.budgets.max_estimated_cost_usd,
            ),
            created_at=now,
            updated_at=now,
        )

    def make_binding(run: Run, identity: RepositoryIdentity) -> RuntimeBinding:
        relative = (Path(config.workspace.root) / run.id.root).as_posix()
        return RuntimeBinding(
            run_id=run.id,
            repository=identity,
            worktree_id=WorktreeId("wt_" + run.id.root.removeprefix("run_")),
            worktree_relative_path=relative,
            branch_name=f"revanent/{run.work_package.id.root}-{run.id.root[-8:]}",
            created_at=run.created_at,
        )

    def make_request(run: Run, revision: int | None) -> OrchestrationRequest:
        binding = storage.get_runtime_binding(run.id)
        worktree_id = binding.worktree_id
        target = effective.repository_root / binding.worktree_relative_path
        worktree = WorktreeCreationRequest(
            source_path=effective.repository_root,
            target_path=target,
            worktree_id=worktree_id,
            branch_name=binding.branch_name,
            run_id=run.id.root,
        )
        roles = [AgentRole.BUILDER, AgentRole.REVIEWER]
        if config.policy.allow_codex_write_repair:
            roles.append(AgentRole.REPAIRER)
        contexts = tuple(
            ContextSelectionRequest(
                request_id=f"runtime.{role.value.lower()}",
                run_id=run.id,
                work_package_id=run.work_package.id,
                task=run.task,
                role=role,
                root=effective.repository_root,
                repository_reference=binding.repository.repository_id,
                worktree_reference=worktree_id.root,
                discovery=ContextDiscoveryInput(),
                trusted_controls=("Task scope and Revanent policy are authoritative.",),
                created_at=datetime.now(UTC),
            )
            for role in sorted(roles, key=lambda item: item.value)
        )
        workspace = WorkspaceReference(
            kind=WorkspaceKind.WORKTREE, reference_id=worktree_id.root, root=target
        )
        artifact_policy = AgentArtifactPolicy(artifact_root_id=f"runtime.{run.id.root}")

        def agent(role: AgentRole, *, write: bool, model: str | None) -> AgentRequest:
            return AgentRequest(
                invocation_id=AgentInvocationId("inv_" + "0" * 32),
                run_id=run.id,
                work_package_id=run.work_package.id,
                attempt_id=AgentAttemptId("attempt_" + "0" * 32),
                attempt_number=1,
                role=role,
                objective=run.task.objective,
                allowed_scope=tuple(ScopePath(item) for item in run.task.allowed_paths),
                forbidden_scope=tuple(ScopePath(item) for item in run.task.forbidden_paths),
                workspace=workspace,
                timeout_seconds=(
                    config.builder.timeout_seconds
                    if role is AgentRole.BUILDER
                    else config.reviewer.timeout_seconds
                ),
                artifact_policy=artifact_policy,
                routing=AgentRouting(model=model),
                expected_capabilities=ExpectedAgentCapabilities(
                    requires_read_only=not write, requires_repository_writes=write
                ),
            )

        commands = tuple(
            ValidationCommand(
                id=ValidationCommandId(f"vcmd_{item.name}"),
                name=item.name,
                executable=item.command[0],
                arguments=item.command[1:],
                classification=ValidationCommandClass.REQUIRED,
            )
            for item in config.validation.commands
        )
        plans = tuple(
            ValidationPlan(
                id=ValidationPlanId(f"vplan_{index:032x}"),
                run_id=run.id,
                work_package_id=run.work_package.id,
                created_at=datetime.now(UTC),
                workspace=workspace,
                commands=commands,
                execution=ValidationExecutionPolicy(),
                artifacts=ValidationArtifactPolicy(
                    root_id=f"validation.{run.id.root}", allow_artifacts=False
                ),
            )
            for index in range(1, run.budgets.max_repair_attempts + 2)
        )
        return OrchestrationRequest(
            run_id=run.id,
            expected_revision=revision,
            context_requests=contexts,
            worktree=worktree,
            builder_request=agent(AgentRole.BUILDER, write=True, model=config.builder.model),
            reviewer_request=agent(AgentRole.REVIEWER, write=False, model=None),
            local_repair_request=agent(AgentRole.BUILDER, write=True, model=config.builder.model),
            codex_repair_request=(
                agent(AgentRole.REPAIRER, write=True, model=None)
                if config.policy.allow_codex_write_repair
                else None
            ),
            validation_plans=plans,
            codex_repair_authorized=config.policy.allow_codex_write_repair,
        )

    return RuntimeComposition(
        runs=storage,
        telemetry=telemetry,
        orchestration=service,
        git=git,
        repository_root=effective.repository_root,
        make_run=make_run,
        make_binding=make_binding,
        make_request=make_request,
    )


def compose_status(effective: EffectiveConfiguration) -> StatusComposition:
    """Open only durable local evidence for the strictly read-only status command."""
    paths = resolve_project_paths(effective)
    storage = SQLiteRunRepository(paths.state_root / "runs.sqlite")
    storage.schema_status()
    runner = controlled_host_runner(effective.repository_root, ("git",))
    git = LocalGitRepository(
        runner=runner,
        path_policy=PathPolicy(_inspection_roots(effective.repository_root)),
        worktree_root=paths.workspace_root,
        ownership_store=WorktreeOwnershipStore(paths.state_root),
    )
    return StatusComposition(
        runs=storage,
        telemetry=TelemetryService(storage),
        git=git,
        repository_root=effective.repository_root,
    )


def compose_report(effective: EffectiveConfiguration) -> ReportComposition:
    """Compose report assembly from the same read-only evidence surface as status."""
    return ReportComposition(
        status=compose_status(effective),
        effective=effective,
        clock=lambda: datetime.now(UTC),
    )


def compose_report_command(effective: EffectiveConfiguration) -> ReportCommandService:
    """Compose the read-only report command without provider or workflow dependencies."""
    return ReportCommandService(
        ReportCommandComposition(
            report_service=EvidenceReportService(compose_report(effective)),
            effective=effective,
            renderer=ReportRenderer(),
            writer=LocalReportArtifactWriter(),
        )
    )
