"""Fail-closed reconciliation for durable intents without outcomes."""

from __future__ import annotations

from datetime import datetime

from revanent.ports.git import GitError, GitRepository, WorktreeLifecycleStatus
from revanent.ports.orchestration import (
    OrchestrationRecord,
    OrchestrationStep,
    ReconciliationResult,
    ReconciliationState,
    WorkspaceAttempt,
)


class SideEffectReconciler:
    """Inspect owned worktree evidence without replaying an external side effect."""

    def __init__(self, repository: GitRepository) -> None:
        self._repository = repository

    def reconcile(
        self,
        intent: OrchestrationRecord,
        *,
        observed_at: datetime,
    ) -> ReconciliationResult:
        if not isinstance(intent.attempt, WorkspaceAttempt):
            # Mutating agent/validation intent without a durable outcome is never replayed.
            state = (
                ReconciliationState.KNOWN_NONE
                if intent.attempt.kind is OrchestrationStep.REVIEW
                else ReconciliationState.AMBIGUOUS
            )
            return ReconciliationResult(
                attempt_id=intent.attempt.attempt_id,
                state=state,
                safe_to_continue=False,
                reason=(
                    "read-only review intent has no durable terminal response"
                    if state is ReconciliationState.KNOWN_NONE
                    else "mutating attempt intent has no durable terminal outcome"
                ),
                observed_at=observed_at,
            )
        request = intent.attempt.request
        try:
            verification = self._repository.verify_owned_worktree(request.worktree_id)
        except GitError:
            return ReconciliationResult(
                attempt_id=intent.attempt.attempt_id,
                state=ReconciliationState.AMBIGUOUS,
                safe_to_continue=False,
                reason="worktree creation intent could not be reconciled safely",
                observed_at=observed_at,
            )
        active = verification.record.lifecycle_status is WorktreeLifecycleStatus.ACTIVE
        matches_intent = (
            active
            and verification.record.worktree_id == request.worktree_id
            and verification.record.run_id == request.run_id
            and verification.record.repository.worktree_root == request.source_path
            and verification.record.worktree_path == request.target_path
            and verification.record.branch_name == request.branch_name
            and verification.worktree.path == request.target_path
            and verification.worktree.branch == request.branch_name
            and verification.repository.identity.repository_id
            == verification.record.repository.repository_id
        )
        return ReconciliationResult(
            attempt_id=intent.attempt.attempt_id,
            state=(
                ReconciliationState.KNOWN_PRESENT
                if matches_intent
                else ReconciliationState.INCOMPATIBLE
            ),
            safe_to_continue=matches_intent,
            reason=(
                "owned worktree exists and matches durable creation intent"
                if matches_intent
                else "live worktree ownership does not match durable creation intent"
            ),
            observed_at=observed_at,
        )
