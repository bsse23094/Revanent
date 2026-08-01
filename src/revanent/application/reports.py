"""Read-only assembly of one bounded evidence report from durable runtime evidence."""

from __future__ import annotations

import hashlib
import platform
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from revanent import __version__
from revanent.application.workflows import (
    RunStatusRequest,
    RunStatusSnapshot,
    RuntimeActionStatus,
    StatusApplicationService,
    StatusComposition,
)
from revanent.config import EffectiveConfiguration
from revanent.domain import RunId, RunState
from revanent.ports.orchestration import (
    ContextAttempt,
    OrchestrationRecord,
    OrchestrationRecordStage,
    ReviewAttempt,
    ValidationAttempt,
    WorkspaceAttempt,
)
from revanent.ports.reporting import (
    EVIDENCE_REPORT_SCHEMA_VERSION,
    MAX_REPORT_ARTIFACTS,
    MAX_REPORT_ATTEMPTS,
    EvidenceReport,
    EvidenceReportRequest,
    EvidenceReportStatus,
    EvidenceSection,
    ReportArtifact,
    ReportAttempt,
    ReportContext,
    ReportFailure,
    ReportFinding,
    ReportReservation,
    ReportReview,
    ReportUsage,
    ReportValidation,
    ReportValidationCommand,
    ReportWorkspace,
    ReproductionEvidence,
    VerificationEvidence,
)
from revanent.ports.validation import ValidationStatus


@dataclass(frozen=True, slots=True)
class ReportComposition:
    """Read-only ports and stable configuration facts needed by report assembly."""

    status: StatusComposition
    effective: EffectiveConfiguration
    clock: Callable[[], datetime]


class EvidenceReportService:
    """Assemble report evidence without reconciling, writing, or invoking external work."""

    def __init__(self, composition: ReportComposition) -> None:
        self._composition = composition

    def generate(self, request: EvidenceReportRequest) -> EvidenceReport:
        generated_at = self._clock()
        if generated_at.tzinfo is None or generated_at.utcoffset() != UTC.utcoffset(generated_at):
            raise ValueError("report clock must return UTC timestamps")
        status = StatusApplicationService(self._composition.status).status(
            RunStatusRequest(run_id=request.run_id)
        )
        if status.action_status is RuntimeActionStatus.NOT_FOUND:
            return self._diagnostic(
                request.run_id, generated_at, EvidenceReportStatus.NOT_FOUND, status
            )
        if status.action_status is RuntimeActionStatus.BLOCKED:
            return self._diagnostic(
                request.run_id, generated_at, EvidenceReportStatus.BLOCKED, status
            )
        if status.action_status is RuntimeActionStatus.INVALID_EVIDENCE:
            return self._diagnostic(
                request.run_id, generated_at, EvidenceReportStatus.INVALID_EVIDENCE, status
            )
        try:
            return self._assemble(request.run_id, generated_at, status)
        except Exception:
            return self._diagnostic(
                request.run_id, generated_at, EvidenceReportStatus.INTERNAL_FAILURE, status
            )

    def _assemble(
        self, run_id: RunId, generated_at: datetime, status: RunStatusSnapshot
    ) -> EvidenceReport:
        repository = self._composition.status.runs
        stored = repository.get_run(run_id)
        binding = repository.get_runtime_binding(run_id)
        records = repository.list_orchestration_records(run_id)
        contexts = _contexts(records)
        latest_validation = _latest(records, ValidationAttempt)
        latest_review = _latest(records, ReviewAttempt)
        workspace = _workspace(records, binding.worktree_id.root, binding.worktree_relative_path)
        validation = _validation(latest_validation, status)
        review = _review(latest_review, status)
        artifacts = _artifacts(records)
        contradictions = list(status.contradiction_codes)
        if stored.run.state is RunState.APPROVED and not (
            status.evidence_complete
            and validation.status is ValidationStatus.PASSED
            and validation.evidence_complete
            and review.approval_gate_valid
            and review.decision == "APPROVABLE"
            and review.unresolved_high_or_critical == 0
            and status.unresolved_reservations == 0
            and not status.in_flight_ambiguity
        ):
            contradictions.append("approval_evidence_incomplete")
        contradiction_codes = tuple(sorted(set(contradictions)))[:64]
        evidence_complete = (
            status.evidence_complete and stored.run.state.is_terminal and not contradiction_codes
        )
        report_status = _report_status(status, evidence_complete, contradiction_codes)
        context = _context(contexts)
        limitations = _limitations(status, report_status)
        return EvidenceReport(
            report_id=_report_id(run_id, stored.revision, generated_at),
            status=report_status,
            run_id=run_id,
            work_package_id=stored.run.work_package.id.root,
            generated_at=generated_at,
            generator_version=__version__,
            run_state=stored.run.state,
            revision=stored.revision,
            created_at=stored.run.created_at,
            updated_at=stored.run.updated_at,
            repository_id=binding.repository.repository_id,
            task_id=stored.run.task.id.root,
            allowed_scope=stored.run.task.allowed_paths,
            forbidden_scope=stored.run.task.forbidden_paths,
            terminal_reason_code=status.reason_code,
            cancellation_requested=status.cancellation_requested,
            cancellation_terminal=status.cancellation_terminal,
            evidence_complete=evidence_complete,
            contradictory_evidence=bool(contradiction_codes),
            contradiction_codes=contradiction_codes,
            sections=_sections(context, validation, review, status, evidence_complete),
            workspace=workspace,
            context=context,
            attempts=_attempts(records),
            validation=validation,
            review=review,
            usage=tuple(
                ReportUsage(
                    metric=item.metric,
                    unit=item.unit,
                    provenance=item.provenance,
                    integer_value=item.integer_value,
                    decimal_value=item.decimal_value,
                    currency=item.currency,
                    unavailable_count=item.unavailable_count,
                )
                for item in status.usage
            ),
            reservations=tuple(
                ReportReservation(
                    reservation_id=item.id,
                    metric=item.metric.value,
                    status=item.status,
                    integer_reserved=item.integer_reserved,
                    decimal_reserved=item.decimal_reserved,
                    currency=item.currency,
                )
                for item in self._composition.status.telemetry.reservations(run_id)[:128]
            ),
            artifacts=artifacts,
            reproduction=_reproduction(self._composition.effective, status),
            verification=VerificationEvidence(
                validation_status=validation.status,
                review_decision=review.decision,
                approval_gate_present=review.approval_gate_present,
                approval_permitted=(
                    stored.run.state is RunState.APPROVED
                    and report_status is EvidenceReportStatus.COMPLETE
                ),
                reason_codes=contradiction_codes or (status.reason_code,),
            ),
            limitations=limitations,
        )

    def _diagnostic(
        self,
        run_id: RunId,
        generated_at: datetime,
        report_status: EvidenceReportStatus,
        status: RunStatusSnapshot,
    ) -> EvidenceReport:
        code = status.failure.code if status.failure is not None else status.reason_code
        return EvidenceReport(
            report_id=_report_id(run_id, status.revision or 0, generated_at),
            status=report_status,
            run_id=run_id,
            generated_at=generated_at,
            generator_version=__version__,
            run_state=status.state,
            revision=status.revision,
            repository_id=status.repository_id,
            terminal_reason_code=code,
            evidence_complete=False,
            contradictory_evidence=report_status is EvidenceReportStatus.INVALID_EVIDENCE,
            contradiction_codes=status.contradiction_codes,
            sections=(EvidenceSection(name="runtime", complete=False, reason_codes=(code,)),),
            reproduction=_reproduction(self._composition.effective, status),
            verification=VerificationEvidence(
                approval_gate_present=False,
                approval_permitted=False,
                reason_codes=(code,),
            ),
            limitations=("runtime_evidence_unavailable",),
            failure=ReportFailure(
                code=code, message="report evidence could not be verified safely"
            ),
        )

    def _clock(self) -> datetime:
        return self._composition.clock()


def _report_id(run_id: RunId, revision: int, generated_at: datetime) -> str:
    material = (
        f"{run_id.root}:{revision}:{generated_at.isoformat()}:v{EVIDENCE_REPORT_SCHEMA_VERSION}"
    )
    return "report_" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _latest[T](records: tuple[OrchestrationRecord, ...], type_: type[T]) -> T | None:
    values = [
        item.attempt
        for item in records
        if item.stage is OrchestrationRecordStage.OUTCOME and isinstance(item.attempt, type_)
    ]
    return values[-1] if values else None


def _contexts(records: tuple[OrchestrationRecord, ...]) -> tuple[ContextAttempt, ...]:
    return tuple(
        item.attempt
        for item in records
        if item.stage is OrchestrationRecordStage.OUTCOME
        and isinstance(item.attempt, ContextAttempt)
        and item.attempt.manifest is not None
    )


def _context(values: tuple[ContextAttempt, ...]) -> ReportContext:
    manifests = tuple(item.manifest for item in values if item.manifest is not None)
    return ReportContext(
        manifest_ids=tuple(item.manifest_id for item in manifests)[:16],
        retained_bytes=sum(item.retained_bytes for item in manifests),
        baseline_bytes=sum(item.baseline_bytes for item in manifests),
        included_count=sum(item.included_count for item in manifests),
        excluded_count=sum(item.excluded_count for item in manifests),
        required_evidence_complete=bool(manifests)
        and all(item.required_evidence_complete for item in manifests),
        complete=bool(manifests) and all(item.status.value == "COMPLETE" for item in manifests),
    )


def _workspace(
    records: tuple[OrchestrationRecord, ...], worktree_id: str, relative_path: str
) -> ReportWorkspace:
    item = _latest(records, WorkspaceAttempt)
    if item is None or item.evidence is None:
        return ReportWorkspace(
            worktree_id=worktree_id,
            relative_path=relative_path,
            ownership_verified=False,
        )
    return ReportWorkspace(
        worktree_id=item.evidence.worktree_id.root,
        relative_path=relative_path,
        branch=item.evidence.branch,
        lifecycle=item.evidence.lifecycle.value,
        ownership_verified=item.evidence.lifecycle.value == "ACTIVE",
    )


def _validation(value: ValidationAttempt | None, status: RunStatusSnapshot) -> ReportValidation:
    if value is None or value.result is None:
        return ReportValidation(
            plan_id=status.validation.plan_id,
            status=status.validation.status,
            required_commands=status.validation.required_commands,
            passed=status.validation.passed,
            failed=status.validation.failed,
            timed_out=status.validation.timed_out,
            cancelled=status.validation.cancelled,
            unavailable=status.validation.unavailable,
            evidence_complete=status.validation.evidence_complete,
        )
    commands = []
    for item in value.result.commands:
        artifacts = []
        for output in (item.stdout, item.stderr):
            if output.artifact is not None:
                artifacts.append(f"{output.artifact.root_id}:{output.artifact.relative_path.root}")
        commands.append(
            ReportValidationCommand(
                command_id=item.command_id.root,
                name=item.executable,
                executable=item.executable,
                classification=item.classification,
                status=item.status,
                expected_exit_codes=item.expected_exit_codes,
                exit_code=item.exit_code,
                duration_ms=item.duration_ms,
                failure_code=item.failure.code if item.failure is not None else None,
                artifact_references=tuple(sorted(artifacts)),
            )
        )
    return ReportValidation(
        plan_id=value.plan.id.root,
        status=value.result.status,
        required_commands=status.validation.required_commands,
        passed=status.validation.passed,
        failed=status.validation.failed,
        timed_out=status.validation.timed_out,
        cancelled=status.validation.cancelled,
        unavailable=status.validation.unavailable,
        evidence_complete=status.validation.evidence_complete,
        commands=tuple(commands),
    )


def _review(value: ReviewAttempt | None, status: RunStatusSnapshot) -> ReportReview:
    if value is None or value.gate_decision is None:
        return ReportReview(
            decision=status.review.decision,
            attempt_status=status.review.attempt_status,
            approval_gate_present=status.review.approval_gate_present,
            approval_gate_valid=False,
            unresolved_high_or_critical=status.review.unresolved_high_or_critical,
        )
    decision = value.gate_decision
    findings = tuple(
        ReportFinding(
            finding_id=item.finding_id,
            severity=item.severity.value,
            summary=_safe_text(item.summary, 256),
        )
        for item in decision.finding_evidence[:128]
    )
    return ReportReview(
        decision=decision.status.value,
        attempt_status=value.status,
        approval_gate_present=status.review.approval_gate_present,
        approval_gate_valid=(
            decision.approval_gate is not None
            and decision.approval_gate.is_satisfied
            and status.review.approval_gate_present
        ),
        unresolved_high_or_critical=status.review.unresolved_high_or_critical,
        reviewer_adapter_id=value.adapter_id.root,
        findings=findings,
    )


def _attempts(records: tuple[OrchestrationRecord, ...]) -> tuple[ReportAttempt, ...]:
    values: list[ReportAttempt] = []
    for record in records:
        if record.stage is not OrchestrationRecordStage.OUTCOME:
            continue
        attempt = record.attempt
        response = getattr(attempt, "response", None)
        artifacts: tuple[str, ...] = ()
        if response is not None:
            artifacts = tuple(
                sorted(f"{item.root_id}:{item.relative_path.root}" for item in response.artifacts)
            )
        values.append(
            ReportAttempt(
                attempt_id=attempt.attempt_id.root,
                kind=attempt.kind,
                sequence=attempt.sequence,
                status=attempt.status,
                side_effects=attempt.side_effects,
                started_at=attempt.started_at,
                completed_at=attempt.completed_at,
                role=_attempt_role(attempt),
                adapter_id=_attempt_adapter(attempt),
                invocation_id=_attempt_invocation(attempt),
                artifact_references=artifacts[:MAX_REPORT_ARTIFACTS],
            )
        )
    return tuple(values[:MAX_REPORT_ATTEMPTS])


def _artifacts(records: tuple[OrchestrationRecord, ...]) -> tuple[ReportArtifact, ...]:
    values: list[ReportArtifact] = []
    for record in records:
        if record.stage is not OrchestrationRecordStage.OUTCOME:
            continue
        response = getattr(record.attempt, "response", None)
        if response is not None:
            for item in response.artifacts:
                values.append(
                    ReportArtifact(
                        reference=f"{item.root_id}:{item.relative_path.root}",
                        content_type=item.content_type,
                        observed_bytes=item.observed_bytes,
                        stored_bytes=item.stored_bytes,
                        digest_sha256=item.sha256,
                        complete=item.status.value == "COMPLETE",
                        correlation=response.invocation_id.root,
                    )
                )
        result = getattr(record.attempt, "result", None)
        if result is not None:
            for command in result.commands:
                for output in (command.stdout, command.stderr):
                    if output.artifact is not None:
                        item = output.artifact
                        values.append(
                            ReportArtifact(
                                reference=f"{item.root_id}:{item.relative_path.root}",
                                content_type="text/plain",
                                observed_bytes=item.observed_source_bytes,
                                stored_bytes=item.stored_bytes,
                                complete=item.status.value == "COMPLETE",
                                correlation=item.correlation_id,
                            )
                        )
    unique = {item.reference: item for item in values}
    return tuple(unique[key] for key in sorted(unique))[:MAX_REPORT_ARTIFACTS]


def _sections(
    context: ReportContext,
    validation: ReportValidation,
    review: ReportReview,
    status: RunStatusSnapshot,
    complete: bool,
) -> tuple[EvidenceSection, ...]:
    return (
        EvidenceSection(name="identity", complete=status.failure is None),
        EvidenceSection(
            name="context",
            complete=context.complete and context.required_evidence_complete,
        ),
        EvidenceSection(name="validation", complete=validation.evidence_complete),
        EvidenceSection(
            name="review",
            complete=review.approval_gate_present or status.state is not RunState.APPROVED,
        ),
        EvidenceSection(name="telemetry", complete=status.unresolved_reservations == 0),
        EvidenceSection(name="overall", complete=complete, reason_codes=status.contradiction_codes),
    )


def _report_status(
    status: RunStatusSnapshot, complete: bool, contradictions: tuple[str, ...]
) -> EvidenceReportStatus:
    if contradictions:
        return EvidenceReportStatus.INVALID_EVIDENCE
    if status.state is RunState.BLOCKED:
        return EvidenceReportStatus.BLOCKED
    if status.state is None or not status.state.is_terminal:
        return EvidenceReportStatus.INCOMPLETE
    if not complete:
        return EvidenceReportStatus.INCOMPLETE
    if status.state is RunState.APPROVED:
        return EvidenceReportStatus.COMPLETE
    return EvidenceReportStatus.COMPLETE_WITH_WARNINGS


def _limitations(status: RunStatusSnapshot, report_status: EvidenceReportStatus) -> tuple[str, ...]:
    values = ["no_live_provider_certification", "no_operating_system_sandbox"]
    if any(item.unavailable_count > 0 for item in status.usage):
        values.append("usage_metrics_unavailable")
    if status.unresolved_reservations:
        values.append("telemetry_reservations_unresolved")
    if status.in_flight_ambiguity:
        values.append("external_side_effects_ambiguous")
    if report_status is EvidenceReportStatus.INCOMPLETE:
        values.append("evidence_incomplete")
    return tuple(sorted(values))


def _reproduction(
    effective: EffectiveConfiguration, status: RunStatusSnapshot
) -> ReproductionEvidence:
    configuration = effective.config.model_dump_json().encode("utf-8")
    return ReproductionEvidence(
        configuration_schema_version=effective.config.schema_version,
        configuration_digest_sha256=hashlib.sha256(configuration).hexdigest(),
        repository_id=status.repository_id,
        worktree_id=(
            status.worktree_reference.rsplit("/", 1)[-1] if status.worktree_reference else None
        ),
        validation_plan_id=status.validation.plan_id,
        validation_command_ids=tuple(item.name for item in effective.config.validation.commands),
        platform=platform.platform(),
        python_version=sys.version.split()[0],
        provider_capabilities=(
            f"{effective.config.builder.provider}:NOT_PROBED",
            f"{effective.config.reviewer.provider}:NOT_PROBED",
        ),
    )


def _safe_text(value: str, limit: int) -> str:
    return "".join(
        character if character >= " " and character != "\x7f" else "?" for character in value
    )[:limit]


def _attempt_role(attempt: object) -> str | None:
    value = getattr(attempt, "role", None)
    return value.value if value is not None else None


def _attempt_invocation(attempt: object) -> str | None:
    value = getattr(attempt, "invocation_id", None)
    return value.root if value is not None else None


def _attempt_adapter(attempt: object) -> str | None:
    value = getattr(attempt, "adapter_id", None)
    return value.root if value is not None else None
