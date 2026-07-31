"""Deterministic P4-001 validation and local-gate fixtures."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from revanent.domain import AgentInvocationId, RunId, WorkPackageId
from revanent.ports import (
    AdapterId,
    ValidationArtifactPolicy,
    ValidationCommand,
    ValidationCommandClass,
    ValidationCommandId,
    ValidationExecutionPolicy,
    ValidationPlan,
    ValidationPlanId,
    WorkspaceKind,
    WorkspaceReference,
)
from revanent.review import LocalApprovalEvidence
from tests.agent_factories import NOW

PLAN_ID = ValidationPlanId(f"vplan_{'4' * 32}")
RUN_ID = RunId(f"run_{'2' * 32}")
WORK_PACKAGE_ID = WorkPackageId("P4-001")
REVIEW_INVOCATION_ID = AgentInvocationId(f"inv_{'1' * 32}")


def make_validation_command(
    name: str = "format",
    *,
    command_id: str = "vcmd_format",
    executable: str = "fixture-python",
    arguments: tuple[str, ...] = ("fixture", "exit", "0"),
    classification: ValidationCommandClass = ValidationCommandClass.REQUIRED,
) -> ValidationCommand:
    return ValidationCommand(
        id=ValidationCommandId(command_id),
        name=name,
        executable=executable,
        arguments=arguments,
        classification=classification,
    )


def make_validation_plan(
    root: Path,
    *,
    commands: tuple[ValidationCommand, ...] | None = None,
    fail_fast: bool = False,
    allow_advisory_failures: bool = False,
    artifact_directory: Path | None = None,
) -> ValidationPlan:
    return ValidationPlan(
        id=PLAN_ID,
        run_id=RUN_ID,
        work_package_id=WORK_PACKAGE_ID,
        created_at=NOW,
        workspace=WorkspaceReference(
            kind=WorkspaceKind.WORKTREE,
            reference_id="worktree.validation",
            root=root.resolve(strict=True),
        ),
        commands=commands or (make_validation_command(),),
        execution=ValidationExecutionPolicy(
            fail_fast=fail_fast,
            allow_advisory_failures=allow_advisory_failures,
        ),
        artifacts=ValidationArtifactPolicy(
            root_id="validation-artifacts.fixture",
            directory=artifact_directory.resolve(strict=True) if artifact_directory else None,
            allow_artifacts=artifact_directory is not None,
        ),
    )


def make_local_approval_evidence(
    *,
    review_invocation_id: AgentInvocationId = REVIEW_INVOCATION_ID,
    observed_at: datetime | None = None,
    scope_justified: bool = True,
    generated_files_consistent: bool = True,
    lockfiles_consistent: bool = True,
    evidence_complete: bool = True,
    required_artifacts_complete: bool = True,
    repository_clean: bool = True,
    review_read_only_verified: bool = True,
    side_effects_reconciled: bool = True,
) -> LocalApprovalEvidence:
    return LocalApprovalEvidence(
        run_id=RUN_ID,
        work_package_id=WORK_PACKAGE_ID,
        validation_plan_id=PLAN_ID,
        review_invocation_id=review_invocation_id,
        review_adapter_id=AdapterId("fake.agent"),
        observed_at=observed_at or NOW + timedelta(seconds=5),
        scope_justified=scope_justified,
        generated_files_consistent=generated_files_consistent,
        lockfiles_consistent=lockfiles_consistent,
        evidence_complete=evidence_complete,
        required_artifacts_complete=required_artifacts_complete,
        repository_clean=repository_clean,
        review_read_only_verified=review_read_only_verified,
        side_effects_reconciled=side_effects_reconciled,
    )
