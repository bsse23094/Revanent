"""Finite durable coordinator over existing Revanent ports and state transitions."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

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
from revanent.ports.git import GitError, GitRepository, WorktreeId, WorktreeLifecycleStatus
from revanent.ports.orchestration import (
    AttemptStatus,
    BuildAttempt,
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
from revanent.ports.validation import ValidationExecutor, ValidationPlanResult, ValidationStatus
from revanent.review import ReviewGate, ReviewGateInput, ReviewGateStatus

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
        self._clock = clock
        self._ids = ids or DeterministicOrchestrationIds()
        self._repair_policy = repair_policy or RepairPolicy()
        self._reconciler = SideEffectReconciler(git)

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
            return self._terminal_result(initial)
        if request.expected_revision is not None and initial.revision != request.expected_revision:
            return self._result(
                initial,
                OrchestrationStatus.STALE,
                "persisted run revision differs from the caller expectation",
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
            if cancellation is not None and cancellation.is_cancelled():
                return self._cancel(stored)
            state = stored.run.state
            if state is RunState.CREATED:
                self._transition(stored, RunState.PLANNING, "orchestration planning started")
            elif state is RunState.PLANNING:
                self._transition(
                    stored,
                    RunState.CONTEXT_PREPARING,
                    "prebuilt bounded context references accepted",
                )
            elif state is RunState.CONTEXT_PREPARING:
                self._transition(
                    stored,
                    RunState.WORKSPACE_PREPARING,
                    "workspace preparation started",
                )
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
            plan = request.validation_plans[sequence - 1]
            attempt_id = self._ids.attempt_id(stored.run.id, OrchestrationStep.VALIDATION, sequence)
            started = self._clock.now()
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
        data.update(
            attempt_id=self._ids.agent_attempt_id(attempt_id),
            invocation_id=self._ids.invocation_id(attempt_id),
            attempt_number=sequence,
        )
        return AgentRequest.model_validate(data)

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
        attempt: WorkspaceAttempt
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
    data.update(public_text="", diagnostics=(), usage=None)
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
