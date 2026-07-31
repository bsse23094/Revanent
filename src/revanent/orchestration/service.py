"""Finite durable coordinator over existing Revanent ports and state transitions."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from revanent.context.models import ContextPackage, ContextSelectionRequest
from revanent.domain import (
    AgentAttemptId,
    AgentInvocationId,
    ApprovalGate,
    AttemptCounterKind,
    EventId,
    RunId,
    RunState,
    permitted_destinations,
    transition_run,
)
from revanent.orchestration.reconciliation import SideEffectReconciler
from revanent.orchestration.repair_policy import RepairPolicy
from revanent.ports.agents import (
    AgentAdapter,
    AgentAvailability,
    AgentRequest,
    AgentResponse,
    AgentRole,
    AgentStatus,
)
from revanent.ports.commands import CancellationToken
from revanent.ports.context import ContextSelectorPort
from revanent.ports.git import GitError, GitRepository, WorktreeId, WorktreeLifecycleStatus
from revanent.ports.orchestration import (
    AttemptStatus,
    BuildAttempt,
    ContextAttempt,
    LimitOutcome,
    LocalEvidenceCollector,
    OrchestrationAttemptId,
    OrchestrationClock,
    OrchestrationFailure,
    OrchestrationIdFactory,
    OrchestrationJournal,
    OrchestrationRecord,
    OrchestrationRecordId,
    OrchestrationRecordStage,
    OrchestrationRequest,
    OrchestrationResult,
    OrchestrationStatus,
    OrchestrationStep,
    ReconciliationResult,
    ReconciliationState,
    RecordWriteResult,
    RepairAttempt,
    RepairDecision,
    RepairPolicyInput,
    RepairStrategy,
    ReviewAttempt,
    ValidationAttempt,
    WorkspaceAttempt,
    WorkspaceEvidence,
    agent_side_effect_state,
    validation_attempt_status,
)
from revanent.ports.storage import ConcurrentRunUpdateError, RunRepository, StoredRun
from revanent.ports.telemetry import (
    BudgetDecision,
    BudgetDecisionStatus,
    BudgetLimit,
    BudgetMetric,
    BudgetPolicy,
    BudgetReservation,
    BudgetSettlement,
    ReservationStatus,
    UsageMetric,
    UsageProvenance,
    UsageRecord,
    UsageSource,
    UsageUnit,
    reservation_id,
    usage_record_id,
)
from revanent.ports.validation import (
    ValidationExecutor,
    ValidationPlan,
    ValidationPlanResult,
    ValidationStatus,
)
from revanent.review import ReviewGate, ReviewGateInput, ReviewGateStatus
from revanent.telemetry import TelemetryService, context_usage_records, provider_usage_records

MAX_COORDINATION_STEPS = 1_024


@dataclass(frozen=True, slots=True)
class OrchestrationAdapters:
    builder: AgentAdapter
    reviewer: AgentAdapter
    local_repair: AgentAdapter
    codex_repair: AgentAdapter | None = None


class DeterministicOrchestrationIds:
    """Derive stable bounded IDs from durable identities and sequence numbers."""

    @staticmethod
    def _hex(value: str, length: int) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]

    def attempt_id(
        self, run_id: RunId, step: OrchestrationStep, sequence: int
    ) -> OrchestrationAttemptId:
        return OrchestrationAttemptId(
            f"oattempt_{self._hex(f'{run_id.root}:{step.value}:{sequence}', 32)}"
        )

    def agent_attempt_id(self, attempt_id: OrchestrationAttemptId) -> AgentAttemptId:
        return AgentAttemptId(f"attempt_{self._hex(f'{attempt_id.root}:agent', 32)}")

    def invocation_id(self, attempt_id: OrchestrationAttemptId) -> AgentInvocationId:
        return AgentInvocationId(f"inv_{self._hex(f'{attempt_id.root}:invocation', 32)}")

    def record_id(
        self, attempt_id: OrchestrationAttemptId, stage: OrchestrationRecordStage
    ) -> OrchestrationRecordId:
        return OrchestrationRecordId(f"orec_{self._hex(f'{attempt_id.root}:{stage.value}', 64)}")


class OrchestrationService:
    """Advance one durable run through a finite fake-first workflow."""

    def __init__(
        self,
        *,
        runs: RunRepository,
        journal: OrchestrationJournal,
        git: GitRepository,
        adapters: OrchestrationAdapters,
        validation: ValidationExecutor,
        review_gate: ReviewGate,
        local_evidence: LocalEvidenceCollector,
        context_selector: ContextSelectorPort,
        telemetry: TelemetryService,
        clock: OrchestrationClock,
        ids: OrchestrationIdFactory | None = None,
        repair_policy: RepairPolicy | None = None,
    ) -> None:
        self._runs = runs
        self._journal = journal
        self._git = git
        self._adapters = adapters
        self._validation = validation
        self._review_gate = review_gate
        self._local_evidence = local_evidence
        self._context_selector = context_selector
        self._telemetry = telemetry
        self._clock = clock
        self._ids = ids or DeterministicOrchestrationIds()
        self._repair_policy = repair_policy or RepairPolicy()
        self._reconciler = SideEffectReconciler(git)
        self._context_packages: dict[tuple[str, AgentRole], ContextPackage] = {}

    def execute(
        self,
        request: OrchestrationRequest,
        *,
        cancellation: CancellationToken | None = None,
    ) -> OrchestrationResult:
        if not isinstance(request, OrchestrationRequest):
            raise TypeError("request must be a validated OrchestrationRequest")
        initial = self._runs.get_run(request.run_id)
        if initial.run.state.is_terminal:
            if request.expected_revision is None or initial.revision == request.expected_revision:
                self._reconcile_telemetry(initial)
            return self._terminal_result(initial)
        if request.expected_revision is not None and initial.revision != request.expected_revision:
            return self._result(
                initial,
                OrchestrationStatus.STALE,
                "persisted run revision differs from the caller expectation",
            )
        self._reconcile_telemetry(initial)
        if initial.run.state.is_terminal:
            return self._terminal_result(initial)
        if self._has_unresolved_reservation(initial):
            return self._terminate(
                initial,
                RunState.BLOCKED,
                "unresolved reservation prevents safe continuation",
            )
        if initial.run.state not in {
            RunState.CREATED,
            RunState.PLANNING,
            RunState.CONTEXT_PREPARING,
        } and not self._restore_all_context(initial, request):
            return self._terminate(
                initial,
                RunState.BLOCKED,
                "durable context evidence cannot be safely materialized",
            )
        for _ in range(MAX_COORDINATION_STEPS):
            stored = self._runs.get_run(request.run_id)
            if stored.run.state.is_terminal:
                return self._terminal_result(stored)
            if self._duration_exhausted(stored):
                return self._terminate(
                    stored,
                    (
                        RunState.FAILED
                        if RunState.FAILED in permitted_destinations(stored.run.state)
                        else RunState.BLOCKED
                    ),
                    "maximum run duration was exhausted",
                    limit=LimitOutcome.RUN_DURATION_EXHAUSTED,
                )
            duration_budget = self._duration_budget_decision(stored)
            if duration_budget.status is not BudgetDecisionStatus.ALLOW:
                if duration_budget.status is BudgetDecisionStatus.DENY_UNRESOLVED_RESERVATION:
                    return self._terminate(
                        stored,
                        RunState.BLOCKED,
                        "unresolved duration reservation prevents safe continuation",
                    )
                return self._terminate(
                    stored,
                    RunState.FAILED,
                    "measured validation duration exhausted the run budget",
                    limit=LimitOutcome.RUN_DURATION_EXHAUSTED,
                )
            if cancellation is not None and cancellation.is_cancelled():
                return self._cancel(stored)
            state = stored.run.state
            if state is RunState.CREATED:
                self._transition(stored, RunState.PLANNING, "orchestration planning started")
            elif state is RunState.PLANNING:
                self._transition(
                    stored,
                    RunState.CONTEXT_PREPARING,
                    "deterministic context preparation started",
                )
            elif state is RunState.CONTEXT_PREPARING:
                outcome = self._context(stored, request)
                if outcome is not None:
                    return outcome
            elif state is RunState.WORKSPACE_PREPARING:
                try:
                    outcome = self._workspace(stored, request)
                except GitError:
                    return self._terminate(
                        stored, RunState.BLOCKED, "owned worktree verification failed"
                    )
                if outcome is not None:
                    return outcome
            elif state is RunState.BUILDING:
                try:
                    outcome = self._build(stored, request, cancellation)
                except GitError:
                    return self._terminate(
                        stored, RunState.BLOCKED, "owned worktree verification failed"
                    )
                if outcome is not None:
                    return outcome
            elif state is RunState.VALIDATING:
                try:
                    outcome = self._validate(stored, request, cancellation)
                except GitError:
                    return self._terminate(
                        stored, RunState.BLOCKED, "owned worktree verification failed"
                    )
                if outcome is not None:
                    return outcome
            elif state is RunState.REVIEWING:
                try:
                    outcome = self._review(stored, request, cancellation)
                except GitError:
                    return self._terminate(
                        stored, RunState.BLOCKED, "owned worktree verification failed"
                    )
                if outcome is not None:
                    return outcome
            elif state is RunState.REPAIRING:
                try:
                    outcome = self._repair(stored, request, cancellation)
                except GitError:
                    return self._terminate(
                        stored, RunState.BLOCKED, "owned worktree verification failed"
                    )
                if outcome is not None:
                    return outcome
        return self._terminate(
            self._runs.get_run(request.run_id),
            RunState.FAILED,
            "static orchestration step bound was exhausted",
        )

    def reconcile(self, request: OrchestrationRequest) -> OrchestrationResult:
        """Resolve one explicitly selected incomplete durable boundary without replaying it."""
        if not isinstance(request, OrchestrationRequest):
            raise TypeError("request must be a validated OrchestrationRequest")
        stored = self._runs.get_run(request.run_id)
        if stored.run.state.is_terminal:
            return self._terminal_result(stored)
        records = self._journal.list_orchestration_records(stored.run.id)
        outcomes = {
            record.attempt.attempt_id
            for record in records
            if record.stage is OrchestrationRecordStage.OUTCOME
        }
        reconciled = {
            record.attempt.attempt_id
            for record in records
            if record.stage is OrchestrationRecordStage.RECONCILIATION
        }
        intent = next(
            (
                record
                for record in records
                if record.stage is OrchestrationRecordStage.INTENT
                and record.attempt.attempt_id not in outcomes
                and record.attempt.attempt_id not in reconciled
            ),
            None,
        )
        if intent is None:
            return self._result(
                stored,
                OrchestrationStatus.IN_PROGRESS,
                "no incomplete orchestration boundary requires reconciliation",
            )
        if intent.expected_state is not stored.run.state or intent.run_revision != stored.revision:
            return self._terminate(
                stored,
                RunState.BLOCKED,
                "persisted attempt stage conflicts with the current durable run",
            )
        reconciliation = self._reconciler.reconcile(intent, observed_at=self._clock.now())
        self._persist_reconciliation(stored, intent, reconciliation)
        if isinstance(intent.attempt, WorkspaceAttempt) and reconciliation.safe_to_continue:
            updated = self._transition(
                stored,
                RunState.BUILDING,
                "worktree creation was reconciled from live ownership evidence",
            )
            return self._result(
                updated,
                OrchestrationStatus.IN_PROGRESS,
                "worktree creation reconciliation permits bounded continuation",
            )
        if intent.attempt.kind in {
            OrchestrationStep.BUILD,
            OrchestrationStep.REPAIR,
            OrchestrationStep.WORKSPACE,
        }:
            return self._terminate(
                stored,
                RunState.BLOCKED,
                "mutating attempt outcome is unresolved and requires human recovery",
            )
        return self._terminate(
            stored,
            RunState.FAILED,
            "non-mutating attempt has incomplete durable evidence",
        )

    def _workspace(
        self, stored: StoredRun, request: OrchestrationRequest
    ) -> OrchestrationResult | None:
        existing = self._attempt_records(stored, OrchestrationStep.WORKSPACE, 1)
        if existing.outcome is not None:
            self._verify_worktree(stored, request)
            self._transition(stored, RunState.BUILDING, "owned worktree is active and verified")
            return None
        if existing.intent is not None:
            return self._result(
                stored,
                OrchestrationStatus.STALE,
                "workspace intent is already owned; explicit reconciliation is required",
            )
        attempt_id = self._ids.attempt_id(stored.run.id, OrchestrationStep.WORKSPACE, 1)
        started = self._clock.now()
        intent = WorkspaceAttempt(
            attempt_id=attempt_id,
            run_id=stored.run.id,
            work_package_id=stored.run.work_package.id,
            sequence=1,
            status=AttemptStatus.INTENDED,
            started_at=started,
            side_effects=ReconciliationState.KNOWN_NONE,
            request=request.worktree,
        )
        if not self._persist(stored, intent, OrchestrationRecordStage.INTENT).created:
            return self._result(
                stored, OrchestrationStatus.STALE, "workspace intent is owned elsewhere"
            )
        self._require_current(stored)
        try:
            created = self._git.create_worktree(request.worktree)
            self._verify_worktree(stored, request)
            evidence = WorkspaceEvidence(
                worktree_id=created.record.worktree_id,
                lifecycle=created.record.lifecycle_status,
                path=str(created.record.worktree_path),
                branch=created.record.branch_name,
                repository_id=created.record.repository.repository_id,
            )
            terminal = WorkspaceAttempt(
                **intent.model_dump(exclude={"status", "completed_at", "side_effects", "evidence"}),
                status=AttemptStatus.COMPLETED,
                completed_at=self._clock.now(),
                side_effects=ReconciliationState.KNOWN_PRESENT,
                evidence=evidence,
            )
        except GitError:
            terminal = WorkspaceAttempt(
                **intent.model_dump(exclude={"status", "completed_at", "side_effects", "failure"}),
                status=AttemptStatus.BLOCKED,
                completed_at=self._clock.now(),
                side_effects=ReconciliationState.AMBIGUOUS,
                failure=self._failure(
                    "worktree_creation_blocked", "owned worktree creation failed safely"
                ),
            )
        self._persist(stored, terminal, OrchestrationRecordStage.OUTCOME)
        if terminal.status is not AttemptStatus.COMPLETED:
            return self._terminate(stored, RunState.BLOCKED, "owned worktree could not be prepared")
        self._transition(stored, RunState.BUILDING, "owned worktree created and verified")
        return None

    def _context(
        self,
        stored: StoredRun,
        request: OrchestrationRequest,
    ) -> OrchestrationResult | None:
        if any(item.task != stored.run.task for item in request.context_requests):
            return self._terminate(
                stored,
                RunState.BLOCKED,
                "context task identity or scope conflicts with the durable run",
            )
        for sequence, context_request in enumerate(request.context_requests, 1):
            existing = self._attempt_records(stored, OrchestrationStep.CONTEXT, sequence)
            if existing.outcome is not None:
                assert isinstance(existing.outcome.attempt, ContextAttempt)
                if existing.outcome.attempt.status is not AttemptStatus.COMPLETED:
                    return self._terminate(
                        stored,
                        RunState.BLOCKED,
                        "persisted context evidence is incomplete or invalid",
                    )
                if not self._restore_context_package(
                    context_request,
                    existing.outcome.attempt,
                ):
                    return self._terminate(
                        stored,
                        RunState.BLOCKED,
                        "current context no longer matches the durable manifest",
                    )
                assert existing.outcome.attempt.completed_at is not None
                assert existing.outcome.attempt.manifest is not None
                self._telemetry.record(
                    context_usage_records(
                        existing.outcome.attempt.manifest,
                        observed_at=existing.outcome.attempt.completed_at,
                    )
                )
                continue
            attempt_id = self._ids.attempt_id(
                stored.run.id,
                OrchestrationStep.CONTEXT,
                sequence,
            )
            started = self._clock.now()
            if existing.intent is None:
                intent = ContextAttempt(
                    attempt_id=attempt_id,
                    run_id=stored.run.id,
                    work_package_id=stored.run.work_package.id,
                    sequence=sequence,
                    status=AttemptStatus.INTENDED,
                    started_at=started,
                    side_effects=ReconciliationState.KNOWN_NONE,
                    request_id=context_request.request_id,
                    role=context_request.role,
                )
                if not self._persist(
                    stored,
                    intent,
                    OrchestrationRecordStage.INTENT,
                ).created:
                    return self._result(
                        stored,
                        OrchestrationStatus.STALE,
                        "context intent is owned elsewhere",
                    )
            else:
                assert isinstance(existing.intent.attempt, ContextAttempt)
                intent = existing.intent.attempt
                if (
                    intent.request_id != context_request.request_id
                    or intent.role is not context_request.role
                ):
                    return self._terminate(
                        stored,
                        RunState.BLOCKED,
                        "context intent conflicts with the current request",
                    )
            self._require_current(stored)
            selected = self._context_selector.select(context_request)
            if selected.failure is not None:
                terminal = ContextAttempt(
                    **intent.model_dump(exclude={"status", "completed_at", "failure", "manifest"}),
                    status=(
                        AttemptStatus.BLOCKED
                        if selected.failure.blocking
                        else AttemptStatus.INVALID
                    ),
                    completed_at=self._clock.now(),
                    failure=self._failure(
                        selected.failure.code,
                        "deterministic context selection did not complete safely",
                    ),
                )
                self._persist(stored, terminal, OrchestrationRecordStage.OUTCOME)
                return self._terminate(
                    stored,
                    RunState.BLOCKED if selected.failure.blocking else RunState.FAILED,
                    "required context is incomplete, unsafe, or unavailable",
                )
            assert selected.package is not None
            package = selected.package
            terminal = ContextAttempt(
                **intent.model_dump(exclude={"status", "completed_at", "failure", "manifest"}),
                status=AttemptStatus.COMPLETED,
                completed_at=self._clock.now(),
                manifest=package.manifest,
            )
            persisted_outcome = self._persist(
                stored, terminal, OrchestrationRecordStage.OUTCOME
            ).record.attempt
            assert isinstance(persisted_outcome, ContextAttempt)
            assert persisted_outcome.manifest is not None
            assert persisted_outcome.completed_at is not None
            self._telemetry.record(
                context_usage_records(
                    persisted_outcome.manifest,
                    observed_at=persisted_outcome.completed_at,
                )
            )
            self._context_packages[(stored.run.id.root, context_request.role)] = package
        self._transition(
            stored,
            RunState.WORKSPACE_PREPARING,
            "deterministic context manifests are complete",
        )
        return None

    def _restore_context_package(
        self,
        request: ContextSelectionRequest,
        attempt: ContextAttempt,
    ) -> bool:
        key = (request.run_id.root, request.role)
        cached = self._context_packages.get(key)
        if cached is not None:
            return cached.manifest == attempt.manifest
        selected = self._context_selector.select(request)
        if selected.package is None or selected.package.manifest != attempt.manifest:
            return False
        self._context_packages[key] = selected.package
        return True

    def _restore_all_context(
        self,
        stored: StoredRun,
        request: OrchestrationRequest,
    ) -> bool:
        records = self._journal.list_orchestration_records(stored.run.id)
        outcomes = {
            record.attempt.role: record.attempt
            for record in records
            if record.stage is OrchestrationRecordStage.OUTCOME
            and isinstance(record.attempt, ContextAttempt)
            and record.attempt.status is AttemptStatus.COMPLETED
        }
        return all(
            context_request.role in outcomes
            and self._restore_context_package(
                context_request,
                outcomes[context_request.role],
            )
            for context_request in request.context_requests
        )

    def _build(
        self,
        stored: StoredRun,
        request: OrchestrationRequest,
        cancellation: CancellationToken | None,
    ) -> OrchestrationResult | None:
        sequence = self._next_attempt_sequence(stored, OrchestrationStep.BUILD)
        if (
            stored.run.attempts.build >= stored.run.budgets.max_build_attempts
            or sequence > stored.run.budgets.max_build_attempts
        ):
            return self._terminate(
                stored,
                RunState.FAILED,
                "builder attempt limit exhausted",
                limit=LimitOutcome.BUILD_ATTEMPTS_EXHAUSTED,
            )
        self._verify_worktree(stored, request)
        terminal = self._agent_attempt(
            stored,
            step=OrchestrationStep.BUILD,
            sequence=sequence,
            prototype=request.builder_request,
            adapter=self._adapters.builder,
            cancellation=cancellation,
        )
        if terminal is None:
            return self._result(
                stored,
                OrchestrationStatus.STALE,
                "builder intent is already owned; explicit reconciliation is required",
            )
        assert isinstance(terminal, BuildAttempt)
        if terminal.status is AttemptStatus.BLOCKED:
            return self._terminate(
                stored,
                RunState.BLOCKED,
                "builder is unavailable or blocked",
                increment_attempt=AttemptCounterKind.BUILD,
            )
        if terminal.status is AttemptStatus.CANCELLED:
            return self._cancel(stored, increment_attempt=AttemptCounterKind.BUILD)
        if terminal.status is AttemptStatus.COMPLETED or terminal.side_effects in {
            ReconciliationState.KNOWN_PRESENT,
            ReconciliationState.AMBIGUOUS,
        }:
            self._transition(
                stored,
                RunState.VALIDATING,
                "builder attempt requires validation",
                increment_attempt=AttemptCounterKind.BUILD,
            )
            return None
        return self._terminate(
            stored,
            RunState.FAILED,
            "builder failed without repository writes",
            increment_attempt=AttemptCounterKind.BUILD,
        )

    def _validate(
        self,
        stored: StoredRun,
        request: OrchestrationRequest,
        cancellation: CancellationToken | None,
    ) -> OrchestrationResult | None:
        self._verify_worktree(stored, request)
        sequence = self._next_attempt_sequence(stored, OrchestrationStep.VALIDATION)
        if sequence > len(request.validation_plans):
            return self._terminate(stored, RunState.FAILED, "no validation plan remains")
        existing = self._attempt_records(stored, OrchestrationStep.VALIDATION, sequence)
        if existing.outcome is not None:
            assert isinstance(existing.outcome.attempt, ValidationAttempt)
            terminal = existing.outcome.attempt
        elif existing.intent is not None:
            return self._result(
                stored,
                OrchestrationStatus.STALE,
                "validation intent is already owned; explicit reconciliation is required",
            )
        else:
            declared_plan = request.validation_plans[sequence - 1]
            started = self._clock.now()
            allowance = self._validation_allowance_ms(stored, started_at=started)
            plan = _cap_validation_plan(declared_plan, allowance)
            if plan is None:
                return self._terminate(
                    stored,
                    RunState.FAILED,
                    "remaining run duration cannot launch mandatory validation",
                    limit=LimitOutcome.RUN_DURATION_EXHAUSTED,
                )
            attempt_id = self._ids.attempt_id(stored.run.id, OrchestrationStep.VALIDATION, sequence)
            intent = ValidationAttempt(
                attempt_id=attempt_id,
                run_id=stored.run.id,
                work_package_id=stored.run.work_package.id,
                sequence=sequence,
                status=AttemptStatus.INTENDED,
                started_at=started,
                side_effects=ReconciliationState.KNOWN_NONE,
                plan=plan,
            )
            if not self._persist(stored, intent, OrchestrationRecordStage.INTENT).created:
                return self._result(
                    stored, OrchestrationStatus.STALE, "validation intent is owned elsewhere"
                )
            self._require_current(stored)
            reservation = self._validation_reservation(
                stored,
                attempt_id=attempt_id,
                reserved_ms=sum(command.timeout_seconds for command in plan.commands) * 1_000,
                created_at=started,
            )
            budget = self._telemetry.reserve(
                reservation, self._budget_policy(stored), expected_revision=stored.revision
            )
            if budget.status is not BudgetDecisionStatus.ALLOW:
                blocked = ValidationAttempt(
                    **intent.model_dump(exclude={"status", "completed_at", "failure"}),
                    status=AttemptStatus.BLOCKED,
                    completed_at=self._clock.now(),
                    failure=self._failure(
                        "validation_budget_denied", budget.reason_code or "budget denied"
                    ),
                )
                self._persist(stored, blocked, OrchestrationRecordStage.OUTCOME)
                if budget.status is BudgetDecisionStatus.DENY_UNRESOLVED_RESERVATION:
                    return self._terminate(
                        stored,
                        RunState.BLOCKED,
                        "unresolved duration reservation prevents validation",
                    )
                return self._terminate(
                    stored,
                    RunState.FAILED,
                    "validation duration reservation was denied",
                    limit=LimitOutcome.RUN_DURATION_EXHAUSTED,
                )
            result = self._validation.execute(plan, started_at=started, cancellation=cancellation)
            projected = _project_validation_result(result)
            terminal = ValidationAttempt(
                **intent.model_dump(
                    exclude={"status", "completed_at", "side_effects", "failure", "result"}
                ),
                status=validation_attempt_status(projected),
                completed_at=projected.completed_at,
                side_effects=ReconciliationState.KNOWN_NONE,
                failure=(
                    None
                    if projected.approvable
                    else self._failure(
                        "validation_not_approvable",
                        "validation did not produce complete passing required evidence",
                    )
                ),
                result=projected,
            )
            self._persist(stored, terminal, OrchestrationRecordStage.OUTCOME)
            self._settle_validation_reservation(reservation, projected, attempt_id)
        assert terminal.result is not None
        if terminal.result.approvable:
            self._transition(stored, RunState.REVIEWING, "required validation passed")
            return None
        if terminal.result.status is ValidationStatus.CANCELLED:
            return self._cancel(stored)
        if terminal.result.status in {ValidationStatus.BLOCKED, ValidationStatus.UNAVAILABLE}:
            return self._terminate(
                stored, RunState.BLOCKED, "required validation tooling is blocked"
            )
        if terminal.result.status in {ValidationStatus.INVALID, ValidationStatus.TIMED_OUT}:
            return self._terminate(
                stored,
                RunState.FAILED,
                "validation evidence is invalid or timed out",
            )
        if self._repair_count(stored.run.id) >= stored.run.budgets.max_repair_attempts:
            return self._terminate(
                stored,
                RunState.FAILED,
                "repair limit exhausted after validation failure",
                limit=LimitOutcome.REPAIR_ATTEMPTS_EXHAUSTED,
            )
        self._transition(stored, RunState.REPAIRING, "validation failure requires repair policy")
        return None

    def _review(
        self,
        stored: StoredRun,
        request: OrchestrationRequest,
        cancellation: CancellationToken | None,
    ) -> OrchestrationResult | None:
        self._verify_worktree(stored, request)
        capabilities = self._adapters.reviewer.capabilities
        if (
            capabilities.availability is not AgentAvailability.AVAILABLE
            or AgentRole.REVIEWER not in capabilities.supported_roles
            or not capabilities.supports_read_only
        ):
            return self._terminate(stored, RunState.BLOCKED, "read-only reviewer is unavailable")
        sequence = self._next_attempt_sequence(stored, OrchestrationStep.REVIEW)
        if (
            stored.run.attempts.review >= stored.run.budgets.max_review_attempts
            or sequence > stored.run.budgets.max_review_attempts
        ):
            return self._terminate(
                stored,
                RunState.FAILED,
                "review attempt limit exhausted",
                limit=LimitOutcome.REVIEW_ATTEMPTS_EXHAUSTED,
            )
        validation = self._latest_validation(stored.run.id)
        if validation is None or validation.result is None or not validation.result.approvable:
            return self._terminate(
                stored, RunState.FAILED, "review lacks passing validation evidence"
            )
        existing = self._attempt_records(stored, OrchestrationStep.REVIEW, sequence)
        if existing.outcome is not None:
            assert isinstance(existing.outcome.attempt, ReviewAttempt)
            terminal = existing.outcome.attempt
        elif existing.intent is not None:
            return self._result(
                stored,
                OrchestrationStatus.STALE,
                "review intent is already owned; explicit reconciliation is required",
            )
        else:
            candidate = self._review_once(
                stored, request, validation, sequence=sequence, cancellation=cancellation
            )
            if candidate is None:
                return self._result(
                    stored, OrchestrationStatus.STALE, "review intent is owned elsewhere"
                )
            terminal = candidate
        if terminal.status is AttemptStatus.BLOCKED and terminal.gate_decision is None:
            return self._terminate(stored, RunState.BLOCKED, "review budget reservation was denied")
        assert terminal.gate_decision is not None
        decision = terminal.gate_decision
        if terminal.status is AttemptStatus.CANCELLED:
            return self._cancel(stored, increment_attempt=AttemptCounterKind.REVIEW)
        if decision.status is ReviewGateStatus.APPROVABLE:
            assert decision.approval_gate is not None
            self._transition(
                stored,
                RunState.APPROVED,
                "local validation and structured review gates passed",
                approval_gate=decision.approval_gate,
                increment_attempt=AttemptCounterKind.REVIEW,
            )
            return None
        if decision.status is ReviewGateStatus.BLOCKED:
            return self._terminate(
                stored,
                RunState.BLOCKED,
                "structured review is blocked",
                increment_attempt=AttemptCounterKind.REVIEW,
            )
        if decision.status is ReviewGateStatus.INVALID_EVIDENCE:
            return self._terminate(
                stored,
                RunState.FAILED,
                "structured review evidence is invalid",
                increment_attempt=AttemptCounterKind.REVIEW,
            )
        if self._repair_count(stored.run.id) >= stored.run.budgets.max_repair_attempts:
            return self._terminate(
                stored,
                RunState.FAILED,
                "repair limit exhausted after review",
                limit=LimitOutcome.REPAIR_ATTEMPTS_EXHAUSTED,
            )
        self._transition(
            stored,
            RunState.REPAIRING,
            "review changes require repair policy",
            increment_attempt=AttemptCounterKind.REVIEW,
        )
        return None

    def _repair(
        self,
        stored: StoredRun,
        request: OrchestrationRequest,
        cancellation: CancellationToken | None,
    ) -> OrchestrationResult | None:
        self._verify_worktree(stored, request)
        sequence = self._next_attempt_sequence(stored, OrchestrationStep.REPAIR)
        if (
            stored.run.attempts.repair >= stored.run.budgets.max_repair_attempts
            or sequence > stored.run.budgets.max_repair_attempts
        ):
            return self._terminate(
                stored,
                RunState.FAILED,
                "repair attempt limit exhausted",
                limit=LimitOutcome.REPAIR_ATTEMPTS_EXHAUSTED,
            )
        policy_input = self._repair_input(stored, request)
        decision = self._repair_policy.decide(policy_input, repair_sequence=sequence)
        if decision.strategy is RepairStrategy.NO_REPAIR:
            return self._terminate(
                stored,
                RunState.FAILED,
                "repair policy refused automatic repair",
                metadata=self._repair_decision_metadata(decision),
            )
        if decision.strategy is RepairStrategy.BLOCKED:
            return self._terminate(
                stored,
                RunState.BLOCKED,
                "repair requires unavailable authority",
                metadata=self._repair_decision_metadata(decision),
            )
        if decision.strategy is RepairStrategy.CODEX_REPAIR and not request.codex_repair_authorized:
            return self._terminate(
                stored, RunState.BLOCKED, "Codex repair lacks explicit authorization"
            )
        prototype = (
            request.local_repair_request
            if decision.strategy is RepairStrategy.LOCAL_BUILDER
            else request.codex_repair_request
        )
        adapter = (
            self._adapters.local_repair
            if decision.strategy is RepairStrategy.LOCAL_BUILDER
            else self._adapters.codex_repair
        )
        if prototype is None or adapter is None:
            return self._terminate(
                stored, RunState.BLOCKED, "selected repair adapter is unavailable"
            )
        terminal = self._agent_attempt(
            stored,
            step=OrchestrationStep.REPAIR,
            sequence=sequence,
            prototype=prototype,
            adapter=adapter,
            cancellation=cancellation,
            decision=decision,
            write_authorized=(decision.strategy is RepairStrategy.CODEX_REPAIR),
        )
        if terminal is None:
            return self._result(
                stored,
                OrchestrationStatus.STALE,
                "repair intent is already owned; explicit reconciliation is required",
            )
        assert isinstance(terminal, RepairAttempt)
        if terminal.status is AttemptStatus.BLOCKED:
            return self._terminate(
                stored,
                RunState.BLOCKED,
                "repair adapter is unavailable or blocked",
                increment_attempt=AttemptCounterKind.REPAIR,
            )
        if terminal.status is AttemptStatus.CANCELLED:
            return self._cancel(stored, increment_attempt=AttemptCounterKind.REPAIR)
        if (
            terminal.status is AttemptStatus.COMPLETED
            or terminal.side_effects is not ReconciliationState.KNOWN_NONE
        ):
            self._transition(
                stored,
                RunState.VALIDATING,
                "repair attempt requires complete revalidation",
                increment_attempt=AttemptCounterKind.REPAIR,
            )
            return None
        return self._terminate(
            stored,
            RunState.FAILED,
            "repair failed without repository writes",
            increment_attempt=AttemptCounterKind.REPAIR,
        )

    def _agent_attempt(
        self,
        stored: StoredRun,
        *,
        step: OrchestrationStep,
        sequence: int,
        prototype: AgentRequest,
        adapter: AgentAdapter,
        cancellation: CancellationToken | None,
        decision: RepairDecision | None = None,
        write_authorized: bool = False,
    ) -> BuildAttempt | RepairAttempt | None:
        existing = self._attempt_records(stored, step, sequence)
        if existing.outcome is not None:
            assert isinstance(existing.outcome.attempt, (BuildAttempt, RepairAttempt))
            return existing.outcome.attempt
        if existing.intent is not None:
            return None
        capabilities = adapter.capabilities
        if (
            capabilities.availability is not AgentAvailability.AVAILABLE
            or prototype.role not in capabilities.supported_roles
        ):
            return self._record_prelaunch_blocked_agent(
                stored,
                step=step,
                sequence=sequence,
                prototype=prototype,
                adapter=adapter,
                decision=decision,
                write_authorized=write_authorized,
            )
        attempt_id = self._ids.attempt_id(stored.run.id, step, sequence)
        request = self._agent_request(prototype, attempt_id, sequence)
        started = self._clock.now()
        common: dict[str, object] = dict(
            attempt_id=attempt_id,
            run_id=stored.run.id,
            work_package_id=stored.run.work_package.id,
            sequence=sequence,
            status=AttemptStatus.INTENDED,
            started_at=started,
            side_effects=ReconciliationState.KNOWN_NONE,
            agent_attempt_id=request.attempt_id,
            invocation_id=request.invocation_id,
            adapter_id=capabilities.adapter_id,
            request=request,
        )
        intent: BuildAttempt | RepairAttempt
        if step is OrchestrationStep.BUILD:
            intent = BuildAttempt.model_validate(common)
        else:
            assert decision is not None
            intent = RepairAttempt.model_validate(
                {
                    **common,
                    "role": request.role,
                    "decision": decision,
                    "write_authorized": write_authorized,
                }
            )
        if not self._persist(stored, intent, OrchestrationRecordStage.INTENT).created:
            return None
        self._require_current(stored)
        budget = self._external_budget_denial(stored)
        reservation = None
        if budget is None:
            reservation = self._agent_reservation(stored, request, step)
            budget = self._telemetry.reserve(
                reservation, self._budget_policy(stored), expected_revision=stored.revision
            )
        if budget.status is not BudgetDecisionStatus.ALLOW:
            terminal_values = {
                **intent.model_dump(exclude={"status", "completed_at", "failure", "response"}),
                "status": AttemptStatus.BLOCKED,
                "completed_at": self._clock.now(),
                "failure": self._failure("budget_denied", budget.reason_code or "budget denied"),
            }
            preflight_terminal = (
                BuildAttempt.model_validate(terminal_values)
                if step is OrchestrationStep.BUILD
                else RepairAttempt.model_validate(terminal_values)
            )
            self._persist(stored, preflight_terminal, OrchestrationRecordStage.OUTCOME)
            return preflight_terminal
        assert reservation is not None
        response = adapter.invoke(request, cancellation=cancellation)
        projected = _project_agent_response(response)
        status = _agent_attempt_status(projected)
        side_effects = self._agent_side_effects(projected, request.role, request)
        terminal_data = intent.model_dump(
            exclude={"status", "completed_at", "side_effects", "failure", "response"}
        )
        terminal_values = {
            **terminal_data,
            "status": status,
            "completed_at": projected.completed_at,
            "side_effects": side_effects,
            "failure": (
                None
                if status is AttemptStatus.COMPLETED
                else self._failure(
                    projected.failure.code if projected.failure is not None else "agent_failed",
                    "agent attempt did not complete successfully",
                )
            ),
            "response": projected,
        }
        terminal: BuildAttempt | RepairAttempt
        if step is OrchestrationStep.BUILD:
            terminal = BuildAttempt.model_validate(terminal_values)
        else:
            terminal = RepairAttempt.model_validate(terminal_values)
        self._persist(stored, terminal, OrchestrationRecordStage.OUTCOME)
        self._settle_agent_reservation(reservation, projected, step)
        return terminal

    def _review_once(
        self,
        stored: StoredRun,
        request: OrchestrationRequest,
        validation: ValidationAttempt,
        *,
        sequence: int,
        cancellation: CancellationToken | None,
    ) -> ReviewAttempt | None:
        adapter = self._adapters.reviewer
        capabilities = adapter.capabilities
        attempt_id = self._ids.attempt_id(stored.run.id, OrchestrationStep.REVIEW, sequence)
        agent_request = self._agent_request(request.reviewer_request, attempt_id, sequence)
        started = self._clock.now()
        assert validation.result is not None
        intent = ReviewAttempt(
            attempt_id=attempt_id,
            run_id=stored.run.id,
            work_package_id=stored.run.work_package.id,
            sequence=sequence,
            status=AttemptStatus.INTENDED,
            started_at=started,
            side_effects=ReconciliationState.KNOWN_NONE,
            agent_attempt_id=agent_request.attempt_id,
            invocation_id=agent_request.invocation_id,
            adapter_id=capabilities.adapter_id,
            request=agent_request,
            validation_plan=validation.plan,
            validation_result=validation.result,
        )
        if not self._persist(stored, intent, OrchestrationRecordStage.INTENT).created:
            return None
        self._require_current(stored)
        budget = self._external_budget_denial(stored)
        reservation = None
        if budget is None:
            reservation = self._agent_reservation(stored, agent_request, OrchestrationStep.REVIEW)
            budget = self._telemetry.reserve(
                reservation, self._budget_policy(stored), expected_revision=stored.revision
            )
        if budget.status is not BudgetDecisionStatus.ALLOW:
            terminal = ReviewAttempt(
                **intent.model_dump(
                    exclude={"status", "completed_at", "failure", "response", "side_effects"}
                ),
                status=AttemptStatus.BLOCKED,
                completed_at=self._clock.now(),
                side_effects=ReconciliationState.KNOWN_NONE,
                failure=self._failure("budget_denied", budget.reason_code or "budget denied"),
            )
            self._persist(stored, terminal, OrchestrationRecordStage.OUTCOME)
            return terminal
        assert reservation is not None
        response = _project_agent_response(adapter.invoke(agent_request, cancellation=cancellation))
        evaluated_at = max(self._clock.now(), response.completed_at, validation.result.completed_at)
        local = self._local_evidence.collect(
            validation_plan=validation.plan,
            validation_result=validation.result,
            reviewer_response=response,
            observed_at=evaluated_at,
        )
        if self._has_unresolved_mutating_side_effects(
            self._journal.list_orchestration_records(stored.run.id)
        ):
            local_data = local.model_dump(mode="python")
            local_data["side_effects_reconciled"] = False
            local = type(local).model_validate(local_data)
        decision = self._review_gate.evaluate(
            ReviewGateInput(
                expected_run_id=stored.run.id,
                expected_work_package_id=stored.run.work_package.id,
                expected_review_invocation_id=agent_request.invocation_id,
                validation_plan=validation.plan,
                validation_result=validation.result,
                reviewer_response=response,
                local_evidence=local,
                evaluated_at=evaluated_at,
            )
        )
        status = (
            AttemptStatus.COMPLETED
            if response.status is AgentStatus.COMPLETED
            else _agent_attempt_status(response)
        )
        terminal = ReviewAttempt(
            **intent.model_dump(
                exclude={
                    "status",
                    "completed_at",
                    "side_effects",
                    "failure",
                    "response",
                    "local_evidence",
                    "gate_decision",
                }
            ),
            status=status,
            completed_at=evaluated_at,
            side_effects=ReconciliationState.KNOWN_NONE,
            failure=(
                None
                if status is AttemptStatus.COMPLETED
                else self._failure("review_failed", "reviewer did not complete")
            ),
            response=response,
            local_evidence=local,
            gate_decision=decision,
        )
        self._persist(stored, terminal, OrchestrationRecordStage.OUTCOME)
        self._settle_agent_reservation(reservation, response, OrchestrationStep.REVIEW)
        return terminal

    def _record_prelaunch_blocked_agent(
        self,
        stored: StoredRun,
        *,
        step: OrchestrationStep,
        sequence: int,
        prototype: AgentRequest,
        adapter: AgentAdapter,
        decision: RepairDecision | None,
        write_authorized: bool,
    ) -> BuildAttempt | RepairAttempt:
        attempt_id = self._ids.attempt_id(stored.run.id, step, sequence)
        request = self._agent_request(prototype, attempt_id, sequence)
        now = self._clock.now()
        common: dict[str, object] = dict(
            attempt_id=attempt_id,
            run_id=stored.run.id,
            work_package_id=stored.run.work_package.id,
            sequence=sequence,
            status=AttemptStatus.BLOCKED,
            started_at=now,
            completed_at=now,
            side_effects=ReconciliationState.KNOWN_NONE,
            failure=self._failure("adapter_unavailable", "required agent adapter is unavailable"),
            agent_attempt_id=request.attempt_id,
            invocation_id=request.invocation_id,
            adapter_id=adapter.capabilities.adapter_id,
            request=request,
        )
        if step is OrchestrationStep.BUILD:
            terminal: BuildAttempt | RepairAttempt = BuildAttempt.model_validate(common)
        else:
            assert decision is not None
            terminal = RepairAttempt.model_validate(
                {
                    **common,
                    "role": request.role,
                    "decision": decision,
                    "write_authorized": write_authorized,
                }
            )
        # Prelaunch refusal has no external side effect but is still durable outcome evidence.
        intent_data = terminal.model_dump(
            exclude={"status", "completed_at", "failure", "side_effects"}
        )
        intent_values = {
            **intent_data,
            "status": AttemptStatus.INTENDED,
            "started_at": now,
            "side_effects": ReconciliationState.KNOWN_NONE,
        }
        intent: BuildAttempt | RepairAttempt
        if step is OrchestrationStep.BUILD:
            intent = BuildAttempt.model_validate(intent_values)
        else:
            intent = RepairAttempt.model_validate(intent_values)
        self._persist(stored, intent, OrchestrationRecordStage.INTENT)
        self._persist(stored, terminal, OrchestrationRecordStage.OUTCOME)
        return terminal

    def _agent_request(
        self,
        prototype: AgentRequest,
        attempt_id: OrchestrationAttemptId,
        sequence: int,
    ) -> AgentRequest:
        data = prototype.model_dump(mode="python")
        package = self._context_packages.get((prototype.run_id.root, prototype.role))
        if package is None:
            raise ValueError("validated context package is unavailable for agent invocation")
        data.update(
            attempt_id=self._ids.agent_attempt_id(attempt_id),
            invocation_id=self._ids.invocation_id(attempt_id),
            attempt_number=sequence,
            context=package.agent_references(),
        )
        return AgentRequest.model_validate(data)

    def _budget_policy(self, stored: StoredRun) -> BudgetPolicy:
        values = [
            BudgetLimit(
                metric=BudgetMetric.BUILD_ATTEMPTS,
                unit=UsageUnit.ATTEMPTS,
                integer_limit=stored.run.budgets.max_build_attempts,
            ),
            BudgetLimit(
                metric=BudgetMetric.REVIEW_ATTEMPTS,
                unit=UsageUnit.ATTEMPTS,
                integer_limit=stored.run.budgets.max_review_attempts,
            ),
            BudgetLimit(
                metric=BudgetMetric.TOTAL_DURATION,
                unit=UsageUnit.MILLISECONDS,
                integer_limit=stored.run.budgets.max_duration_seconds * 1_000,
            ),
        ]
        if stored.run.budgets.max_repair_attempts > 0:
            values.append(
                BudgetLimit(
                    metric=BudgetMetric.REPAIR_ATTEMPTS,
                    unit=UsageUnit.ATTEMPTS,
                    integer_limit=stored.run.budgets.max_repair_attempts,
                )
            )
        if stored.run.budgets.max_remote_tokens is not None:
            values.append(
                BudgetLimit(
                    metric=BudgetMetric.REMOTE_TOKENS,
                    unit=UsageUnit.TOKENS,
                    integer_limit=stored.run.budgets.max_remote_tokens,
                )
            )
        if stored.run.budgets.max_estimated_cost_usd is not None:
            values.append(
                BudgetLimit(
                    metric=BudgetMetric.ESTIMATED_COST,
                    unit=UsageUnit.DECIMAL_CURRENCY,
                    decimal_limit=stored.run.budgets.max_estimated_cost_usd,
                    currency="USD",
                )
            )
        return BudgetPolicy(limits=tuple(sorted(values, key=lambda item: item.metric.value)))

    @staticmethod
    def _external_budget_denial(stored: StoredRun) -> BudgetDecision | None:
        if stored.run.budgets.max_remote_tokens is not None:
            return BudgetDecision(
                status=BudgetDecisionStatus.DENY_USAGE_UNAVAILABLE,
                metric=BudgetMetric.REMOTE_TOKENS,
                unit=UsageUnit.TOKENS,
                reason_code="remote_token_ceiling_unavailable",
            )
        if stored.run.budgets.max_estimated_cost_usd is not None:
            return BudgetDecision(
                status=BudgetDecisionStatus.DENY_USAGE_UNAVAILABLE,
                metric=BudgetMetric.ESTIMATED_COST,
                unit=UsageUnit.DECIMAL_CURRENCY,
                reason_code="estimated_cost_ceiling_unavailable",
            )
        return None

    def _agent_reservation(
        self, stored: StoredRun, request: AgentRequest, step: OrchestrationStep
    ) -> BudgetReservation:
        metric = {
            OrchestrationStep.BUILD: BudgetMetric.BUILD_ATTEMPTS,
            OrchestrationStep.REPAIR: BudgetMetric.REPAIR_ATTEMPTS,
            OrchestrationStep.REVIEW: BudgetMetric.REVIEW_ATTEMPTS,
        }[step]
        key = f"{request.invocation_id.root}:{step.value.lower()}:attempt"
        return BudgetReservation(
            id=reservation_id(stored.run.id, key, metric),
            run_id=stored.run.id,
            work_package_id=stored.run.work_package.id,
            metric=metric,
            unit=UsageUnit.ATTEMPTS,
            operation=step.value,
            idempotency_key=key,
            created_at=self._clock.now(),
            integer_reserved=1,
            attempt_id=request.attempt_id,
            invocation_id=request.invocation_id,
        )

    def _settle_agent_reservation(
        self, reservation: BudgetReservation, response: AgentResponse, step: OrchestrationStep
    ) -> None:
        metric = {
            OrchestrationStep.BUILD: UsageMetric.BUILD_ATTEMPTS,
            OrchestrationStep.REPAIR: UsageMetric.REPAIR_ATTEMPTS,
            OrchestrationStep.REVIEW: UsageMetric.REVIEW_ATTEMPTS,
        }[step]
        attempt = UsageRecord(
            id=usage_record_id(response.run_id, f"{response.invocation_id.root}:attempt", metric),
            run_id=response.run_id,
            work_package_id=response.work_package_id,
            metric=metric,
            unit=UsageUnit.ATTEMPTS,
            provenance=UsageProvenance.MEASURED,
            source=UsageSource.ORCHESTRATION,
            observed_at=response.completed_at,
            correlation_key=f"{response.invocation_id.root}:attempt",
            integer_value=1,
            attempt_id=response.attempt_id,
            invocation_id=response.invocation_id,
        )
        settlement = BudgetSettlement(
            reservation_id=reservation.id,
            settled_at=response.completed_at,
            integer_consumed=1,
            status=ReservationStatus.SETTLED,
        )
        self._telemetry.settle(
            reservation, settlement, (attempt, *provider_usage_records(response))
        )

    def _validation_reservation(
        self,
        stored: StoredRun,
        *,
        attempt_id: OrchestrationAttemptId,
        reserved_ms: int,
        created_at: datetime,
    ) -> BudgetReservation:
        key = f"{attempt_id.root}:validation_duration"
        return BudgetReservation(
            id=reservation_id(stored.run.id, key, BudgetMetric.TOTAL_DURATION),
            run_id=stored.run.id,
            work_package_id=stored.run.work_package.id,
            metric=BudgetMetric.TOTAL_DURATION,
            unit=UsageUnit.MILLISECONDS,
            operation=OrchestrationStep.VALIDATION.value,
            idempotency_key=key,
            created_at=created_at,
            integer_reserved=reserved_ms,
        )

    def _settle_validation_reservation(
        self,
        reservation: BudgetReservation,
        result: ValidationPlanResult,
        attempt_id: OrchestrationAttemptId,
    ) -> None:
        elapsed_ms = _elapsed_milliseconds(result.started_at, result.completed_at)
        correlation = f"{attempt_id.root}:validation_duration"
        usage = UsageRecord(
            id=usage_record_id(result.run_id, correlation, UsageMetric.VALIDATION_DURATION),
            run_id=result.run_id,
            work_package_id=result.work_package_id,
            metric=UsageMetric.VALIDATION_DURATION,
            unit=UsageUnit.MILLISECONDS,
            provenance=UsageProvenance.MEASURED,
            source=UsageSource.VALIDATION,
            observed_at=result.completed_at,
            correlation_key=correlation,
            integer_value=elapsed_ms,
            reason_code=(
                "validation_duration_overage"
                if elapsed_ms > (reservation.integer_reserved or 0)
                else None
            ),
        )
        settlement = BudgetSettlement(
            reservation_id=reservation.id,
            settled_at=result.completed_at,
            integer_consumed=elapsed_ms,
            status=ReservationStatus.SETTLED,
            reason_code=(
                "validation_duration_overage"
                if elapsed_ms > (reservation.integer_reserved or 0)
                else None
            ),
        )
        self._telemetry.settle(reservation, settlement, (usage,))

    def _duration_budget_decision(self, stored: StoredRun) -> BudgetDecision:
        return self._telemetry.decision(
            run_id=stored.run.id,
            policy=self._budget_policy(stored),
            metric=BudgetMetric.TOTAL_DURATION,
            requested_integer=1,
        )

    def _has_unresolved_reservation(self, stored: StoredRun) -> bool:
        return any(
            reservation.status is ReservationStatus.UNRESOLVED
            for reservation in self._telemetry.reservations(stored.run.id)
        )

    def _validation_allowance_ms(self, stored: StoredRun, *, started_at: datetime) -> int:
        decision = self._duration_budget_decision(stored)
        if decision.status is not BudgetDecisionStatus.ALLOW:
            return 0
        wall_remaining = stored.run.budgets.max_duration_seconds * 1_000 - _elapsed_milliseconds(
            stored.run.created_at, started_at
        )
        return max(0, min(wall_remaining, decision.remaining_integer or 0))

    def _reconcile_telemetry(self, stored: StoredRun) -> None:
        """Perform one finite provider-neutral pass over active reservations."""
        records = self._journal.list_orchestration_records(stored.run.id)
        outcomes = {
            record.attempt.attempt_id: record.attempt
            for record in records
            if record.stage is OrchestrationRecordStage.OUTCOME
        }
        for reservation in self._telemetry.reservations(stored.run.id):
            if reservation.status is not ReservationStatus.ACTIVE:
                continue
            matched = next(
                (
                    attempt
                    for attempt in outcomes.values()
                    if (
                        reservation.invocation_id is not None
                        and isinstance(attempt, (BuildAttempt, RepairAttempt, ReviewAttempt))
                        and attempt.invocation_id == reservation.invocation_id
                    )
                    or (
                        isinstance(attempt, ValidationAttempt)
                        and reservation.id
                        == reservation_id(
                            stored.run.id,
                            f"{attempt.attempt_id.root}:validation_duration",
                            BudgetMetric.TOTAL_DURATION,
                        )
                    )
                ),
                None,
            )
            if isinstance(matched, (BuildAttempt, RepairAttempt, ReviewAttempt)):
                if matched.response is not None:
                    self._settle_agent_reservation(reservation, matched.response, matched.kind)
                    continue
            elif isinstance(matched, ValidationAttempt) and matched.result is not None:
                self._settle_validation_reservation(reservation, matched.result, matched.attempt_id)
                continue
            self._telemetry.mark_unresolved(
                reservation,
                observed_at=max(self._clock.now(), reservation.created_at),
                reason_code="trusted_outcome_missing",
            )

    def _agent_side_effects(
        self,
        response: AgentResponse,
        role: AgentRole,
        request: AgentRequest,
    ) -> ReconciliationState:
        if role is AgentRole.REVIEWER:
            return ReconciliationState.KNOWN_NONE
        if response.failure is not None:
            return agent_side_effect_state(response.failure.side_effects)
        try:
            verified = self._git.verify_owned_worktree(
                self._worktree_id_from_reference(request.workspace.reference_id)
            )
        except (GitError, ValueError):
            return ReconciliationState.AMBIGUOUS
        if (
            verified.repository.status.has_changes
            or verified.worktree.head_commit != verified.record.created_head
        ):
            return ReconciliationState.KNOWN_PRESENT
        return ReconciliationState.KNOWN_NONE

    @staticmethod
    def _worktree_id_from_reference(reference: str) -> WorktreeId:
        return WorktreeId(reference)

    def _verify_worktree(self, stored: StoredRun, request: OrchestrationRequest) -> None:
        verified = self._git.verify_owned_worktree(request.worktree.worktree_id)
        if (
            verified.record.lifecycle_status is not WorktreeLifecycleStatus.ACTIVE
            or verified.record.run_id != stored.run.id.root
            or verified.record.worktree_path != request.worktree.target_path
            or verified.record.branch_name != request.worktree.branch_name
        ):
            raise GitError("owned worktree verification did not match orchestration request")

    def _repair_input(self, stored: StoredRun, request: OrchestrationRequest) -> RepairPolicyInput:
        records = self._journal.list_orchestration_records(stored.run.id)
        fingerprint_sets: list[tuple[str, ...]] = []
        high_risk = False
        for record in records:
            if record.stage is not OrchestrationRecordStage.OUTCOME:
                continue
            if isinstance(record.attempt, ValidationAttempt) and record.attempt.result is not None:
                validation_fingerprints: list[str] = []
                for command in record.attempt.result.commands:
                    if command.status is not ValidationStatus.PASSED:
                        code = command.failure.code if command.failure is not None else "unknown"
                        validation_fingerprints.append(
                            f"validation:{command.command_id.root}:{code}"
                        )
                if validation_fingerprints:
                    fingerprint_sets.append(tuple(sorted(set(validation_fingerprints))))
                high_risk = high_risk or any(
                    spec.security_critical and result.status is not ValidationStatus.PASSED
                    for spec, result in zip(
                        record.attempt.plan.commands,
                        record.attempt.result.commands,
                        strict=True,
                    )
                )
            if (
                isinstance(record.attempt, ReviewAttempt)
                and record.attempt.gate_decision is not None
            ):
                review_fingerprints = tuple(
                    sorted(
                        {
                            f"review:{reason.value}"
                            for reason in record.attempt.gate_decision.reasons
                        }
                    )
                )
                if review_fingerprints:
                    fingerprint_sets.append(review_fingerprints)
                high_risk = high_risk or any(
                    reason.value == "review_high_finding"
                    for reason in record.attempt.gate_decision.reasons
                )
        latest_fingerprints = fingerprint_sets[-1] if fingerprint_sets else ()
        occurrence_count = sum(item == latest_fingerprints for item in fingerprint_sets[:-1])
        malformed = (
            sum(
                isinstance(record.attempt, (BuildAttempt, RepairAttempt))
                and record.stage is OrchestrationRecordStage.OUTCOME
                and record.attempt.response is not None
                and record.attempt.response.status is AgentStatus.INVALID_OUTPUT
                for record in records
            )
            > 1
        )
        local_available = (
            self._adapters.local_repair.capabilities.availability is AgentAvailability.AVAILABLE
            and AgentRole.BUILDER in self._adapters.local_repair.capabilities.supported_roles
            and self._adapters.local_repair.capabilities.supports_repository_writes
        )
        codex_available = (
            self._adapters.codex_repair is not None
            and self._adapters.codex_repair.capabilities.availability is AgentAvailability.AVAILABLE
            and AgentRole.REPAIRER in self._adapters.codex_repair.capabilities.supported_roles
            and self._adapters.codex_repair.capabilities.supports_repository_writes
            and self._adapters.codex_repair.capabilities.supports_repair
        )
        latest_review = next(
            (
                record.attempt
                for record in reversed(records)
                if isinstance(record.attempt, ReviewAttempt)
                and record.stage is OrchestrationRecordStage.OUTCOME
            ),
            None,
        )
        scope_valid = (
            latest_review.local_evidence.scope_justified
            if latest_review is not None and latest_review.local_evidence is not None
            else True
        )
        return RepairPolicyInput(
            defect_fingerprints=latest_fingerprints,
            repeated_defect_count=occurrence_count,
            high_risk=high_risk,
            malformed_builder_repeated=malformed,
            local_builder_available=local_available,
            codex_repair_available=codex_available,
            codex_repair_authorized=request.codex_repair_authorized,
            repairs_remaining=stored.run.budgets.max_repair_attempts
            - self._repair_count(stored.run.id),
            side_effects_reconciled=not self._has_unresolved_mutating_side_effects(records),
            scope_valid=scope_valid,
            evidence_valid=True,
        )

    def _attempt_records(
        self, stored: StoredRun, step: OrchestrationStep, sequence: int
    ) -> _AttemptRecords:
        records = self._journal.list_orchestration_records(stored.run.id)
        return _AttemptRecords(
            intent=next(
                (
                    item
                    for item in records
                    if item.attempt.kind is step
                    and item.attempt.sequence == sequence
                    and item.stage is OrchestrationRecordStage.INTENT
                ),
                None,
            ),
            outcome=next(
                (
                    item
                    for item in records
                    if item.attempt.kind is step
                    and item.attempt.sequence == sequence
                    and item.stage is OrchestrationRecordStage.OUTCOME
                ),
                None,
            ),
        )

    def _persist(
        self,
        stored: StoredRun,
        attempt: ContextAttempt
        | WorkspaceAttempt
        | BuildAttempt
        | ValidationAttempt
        | ReviewAttempt
        | RepairAttempt,
        stage: OrchestrationRecordStage,
    ) -> RecordWriteResult:
        records = self._journal.list_orchestration_records(stored.run.id)
        record = OrchestrationRecord(
            id=self._ids.record_id(attempt.attempt_id, stage),
            run_id=stored.run.id,
            work_package_id=stored.run.work_package.id,
            sequence=len(records) + 1,
            run_revision=stored.revision,
            expected_state=stored.run.state,
            stage=stage,
            occurred_at=self._clock.now(),
            attempt=attempt,
        )
        return self._journal.persist_orchestration_record(stored, record)

    def _persist_reconciliation(
        self,
        stored: StoredRun,
        intent: OrchestrationRecord,
        reconciliation: ReconciliationResult,
    ) -> None:
        records = self._journal.list_orchestration_records(stored.run.id)
        record = OrchestrationRecord(
            id=self._ids.record_id(
                intent.attempt.attempt_id, OrchestrationRecordStage.RECONCILIATION
            ),
            run_id=stored.run.id,
            work_package_id=stored.run.work_package.id,
            sequence=len(records) + 1,
            run_revision=stored.revision,
            expected_state=stored.run.state,
            stage=OrchestrationRecordStage.RECONCILIATION,
            occurred_at=reconciliation.observed_at,
            attempt=intent.attempt,
            reconciliation=reconciliation,
        )
        self._journal.persist_orchestration_record(stored, record)

    def _transition(
        self,
        stored: StoredRun,
        destination: RunState,
        reason: str,
        *,
        approval_gate: ApprovalGate | None = None,
        increment_attempt: AttemptCounterKind | None = None,
        metadata: dict[str, str] | None = None,
    ) -> StoredRun:
        occurred_at = max(self._clock.now(), stored.run.updated_at)
        result = transition_run(
            stored.run,
            destination,
            occurred_at=occurred_at,
            reason=reason,
            metadata={
                "coordinator": "p4-002",
                "revision": str(stored.revision),
                **(metadata or {}),
            },
            approval_gate=approval_gate,
            increment_attempt=increment_attempt,
        )
        event_id = EventId(
            "event_"
            + hashlib.sha256(
                f"{stored.run.id.root}:{stored.revision}:{destination.value}".encode()
            ).hexdigest()[:32]
        )
        return self._runs.persist_transition(stored, result, event_id=event_id)

    def _terminate(
        self,
        stored: StoredRun,
        state: RunState,
        reason: str,
        *,
        limit: LimitOutcome = LimitOutcome.NONE,
        increment_attempt: AttemptCounterKind | None = None,
        metadata: dict[str, str] | None = None,
    ) -> OrchestrationResult:
        transition_metadata = dict(metadata or {})
        if limit is not LimitOutcome.NONE:
            transition_metadata["limit_outcome"] = limit.value
        updated = self._transition(
            stored,
            state,
            reason,
            increment_attempt=increment_attempt,
            metadata=transition_metadata,
        )
        return self._result(updated, _status_for_state(state), reason, limit=limit)

    def _cancel(
        self,
        stored: StoredRun,
        *,
        increment_attempt: AttemptCounterKind | None = None,
    ) -> OrchestrationResult:
        records = self._journal.list_orchestration_records(stored.run.id)
        if self._has_unresolved_mutating_side_effects(records):
            reason = "cancellation preserved an unresolved mutating attempt for human recovery"
        else:
            reason = "cancellation was observed before the next side effect"
        updated = self._transition(
            stored,
            RunState.CANCELLED,
            reason,
            increment_attempt=increment_attempt,
        )
        return self._result(updated, OrchestrationStatus.CANCELLED, reason)

    def _terminal_result(self, stored: StoredRun) -> OrchestrationResult:
        limit = LimitOutcome.NONE
        events = self._runs.list_events(stored.run.id)
        if events:
            metadata = {item.key: item.value for item in events[-1].transition.metadata}
            persisted_limit = metadata.get("limit_outcome")
            if persisted_limit is not None:
                limit = LimitOutcome(persisted_limit)
        return self._result(
            stored,
            _status_for_state(stored.run.state),
            f"run is terminal in {stored.run.state.value}",
            limit=limit,
        )

    def _result(
        self,
        stored: StoredRun,
        status: OrchestrationStatus,
        reason: str,
        *,
        limit: LimitOutcome = LimitOutcome.NONE,
    ) -> OrchestrationResult:
        return OrchestrationResult(
            status=status,
            run=stored.run,
            revision=stored.revision,
            records=self._journal.list_orchestration_records(stored.run.id),
            limit_outcome=limit,
            reason=reason,
        )

    def _next_attempt_sequence(self, stored: StoredRun, step: OrchestrationStep) -> int:
        records = self._journal.list_orchestration_records(stored.run.id)
        current_boundary = next(
            (
                record.attempt.sequence
                for record in reversed(records)
                if record.attempt.kind is step
                and record.run_revision == stored.revision
                and record.expected_state is stored.run.state
                and record.stage
                in {OrchestrationRecordStage.INTENT, OrchestrationRecordStage.OUTCOME}
            ),
            None,
        )
        if current_boundary is not None:
            return current_boundary
        outcomes = {
            record.attempt.attempt_id
            for record in records
            if record.stage is OrchestrationRecordStage.OUTCOME
        }
        pending = next(
            (
                record.attempt.sequence
                for record in records
                if record.stage is OrchestrationRecordStage.INTENT
                and record.attempt.kind is step
                and record.attempt.attempt_id not in outcomes
            ),
            None,
        )
        if pending is not None:
            return pending
        return 1 + sum(
            record.stage is OrchestrationRecordStage.INTENT and record.attempt.kind is step
            for record in records
        )

    def _repair_count(self, run_id: RunId) -> int:
        return sum(
            record.stage is OrchestrationRecordStage.INTENT
            and record.attempt.kind is OrchestrationStep.REPAIR
            for record in self._journal.list_orchestration_records(run_id)
        )

    def _latest_validation(self, run_id: RunId) -> ValidationAttempt | None:
        return next(
            (
                record.attempt
                for record in reversed(self._journal.list_orchestration_records(run_id))
                if record.stage is OrchestrationRecordStage.OUTCOME
                and isinstance(record.attempt, ValidationAttempt)
            ),
            None,
        )

    def _duration_exhausted(self, stored: StoredRun) -> bool:
        return (self._clock.now() - stored.run.created_at).total_seconds() >= (
            stored.run.budgets.max_duration_seconds
        )

    def _require_current(self, stored: StoredRun) -> None:
        current = self._runs.get_run(stored.run.id)
        if current != stored:
            raise ConcurrentRunUpdateError(stored.run.id, stored.revision, current.revision)

    @staticmethod
    def _has_pending_mutating_intent(records: tuple[OrchestrationRecord, ...]) -> bool:
        resolved = {
            record.attempt.attempt_id
            for record in records
            if record.stage is OrchestrationRecordStage.OUTCOME
            or (
                record.stage is OrchestrationRecordStage.RECONCILIATION
                and record.reconciliation is not None
                and record.reconciliation.safe_to_continue
            )
        }
        return any(
            record.stage is OrchestrationRecordStage.INTENT
            and record.attempt.kind
            in {OrchestrationStep.WORKSPACE, OrchestrationStep.BUILD, OrchestrationStep.REPAIR}
            and record.attempt.attempt_id not in resolved
            for record in records
        )

    @classmethod
    def _has_unresolved_mutating_side_effects(
        cls, records: tuple[OrchestrationRecord, ...]
    ) -> bool:
        if cls._has_pending_mutating_intent(records):
            return True
        reconciled = {
            record.attempt.attempt_id
            for record in records
            if record.stage is OrchestrationRecordStage.RECONCILIATION
            and record.reconciliation is not None
            and record.reconciliation.safe_to_continue
        }
        return any(
            record.stage is OrchestrationRecordStage.OUTCOME
            and record.attempt.kind
            in {OrchestrationStep.WORKSPACE, OrchestrationStep.BUILD, OrchestrationStep.REPAIR}
            and record.attempt.side_effects
            in {ReconciliationState.AMBIGUOUS, ReconciliationState.INCOMPATIBLE}
            and record.attempt.attempt_id not in reconciled
            for record in records
        )

    @staticmethod
    def _repair_decision_metadata(decision: RepairDecision) -> dict[str, str]:
        return {
            "repair_strategy": decision.strategy.value,
            "repair_reasons": ",".join(reason.value for reason in decision.reasons),
            "repair_sequence": str(decision.repair_sequence),
        }

    @staticmethod
    def _failure(code: str, message: str) -> OrchestrationFailure:
        return OrchestrationFailure(code=code, message=message)


@dataclass(frozen=True, slots=True)
class _AttemptRecords:
    intent: OrchestrationRecord | None
    outcome: OrchestrationRecord | None


def _elapsed_milliseconds(started_at: datetime, completed_at: datetime) -> int:
    """Return a nonnegative, conservative integer duration from trusted UTC evidence."""
    delta = completed_at - started_at
    microseconds = delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds
    if microseconds < 0:
        raise ValueError("duration completion precedes its start")
    return (microseconds + 999) // 1_000


def _cap_validation_plan(plan: ValidationPlan, allowance_ms: int) -> ValidationPlan | None:
    """Derive a plan whose declared aggregate timeout fits the finite allowance."""
    available_seconds = min(
        allowance_ms // 1_000,
        sum(command.timeout_seconds for command in plan.commands),
    )
    if available_seconds < len(plan.commands):
        return None
    remaining = available_seconds
    commands = []
    for index, command in enumerate(plan.commands):
        commands_after = len(plan.commands) - index - 1
        timeout_seconds = min(command.timeout_seconds, remaining - commands_after)
        command_data = command.model_dump(mode="python")
        command_data["timeout_seconds"] = timeout_seconds
        commands.append(type(command).model_validate(command_data))
        remaining -= timeout_seconds
    plan_data = plan.model_dump(mode="python")
    plan_data["commands"] = tuple(commands)
    return ValidationPlan.model_validate(plan_data)


def _agent_attempt_status(response: AgentResponse) -> AttemptStatus:
    return {
        AgentStatus.COMPLETED: AttemptStatus.COMPLETED,
        AgentStatus.BLOCKED: AttemptStatus.BLOCKED,
        AgentStatus.UNAVAILABLE: AttemptStatus.BLOCKED,
        AgentStatus.CANCELLED: AttemptStatus.CANCELLED,
        AgentStatus.INVALID_OUTPUT: AttemptStatus.INVALID,
        AgentStatus.TIMED_OUT: AttemptStatus.FAILED,
        AgentStatus.FAILED: AttemptStatus.FAILED,
    }[response.status]


def _project_agent_response(response: AgentResponse) -> AgentResponse:
    data = response.model_dump(mode="python")
    data.update(public_text="", diagnostics=())
    return AgentResponse.model_validate(data)


def _project_validation_result(result: ValidationPlanResult) -> ValidationPlanResult:
    data = result.model_dump(mode="python")
    commands = []
    for command in result.commands:
        command_data = command.model_dump(mode="python")
        for stream in ("stdout", "stderr"):
            output = command_data[stream]
            output["text"] = ""
            command_data[stream] = output
        commands.append(command_data)
    data["commands"] = tuple(commands)
    return ValidationPlanResult.model_validate(data)


def _status_for_state(state: RunState) -> OrchestrationStatus:
    return {
        RunState.APPROVED: OrchestrationStatus.APPROVED,
        RunState.FAILED: OrchestrationStatus.FAILED,
        RunState.BLOCKED: OrchestrationStatus.BLOCKED,
        RunState.CANCELLED: OrchestrationStatus.CANCELLED,
    }[state]
