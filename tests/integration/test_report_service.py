from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from revanent.application.reports import EvidenceReportService, ReportComposition
from revanent.application.workflows import StatusComposition
from revanent.config import ConfigurationSource, EffectiveConfiguration, default_config
from revanent.domain import RunState
from revanent.ports import CommandStatus
from revanent.ports.reporting import EvidenceReportRequest, EvidenceReportStatus
from revanent.telemetry import TelemetryService
from tests.e2e.test_orchestration import NOW, RUN_ID, _approved_review, _harness


def test_report_uses_durable_status_evidence_without_mutating_it(tmp_path: Path) -> None:
    service, request, repository, git, *_ = _harness(
        tmp_path,
        validation_statuses=(CommandStatus.SUCCESS,),
        review_results=(_approved_review(),),
        bind_runtime=True,
    )
    assert service.execute(request).status.value == "APPROVED"
    source = request.worktree.source_path
    effective = EffectiveConfiguration(
        repository_root=source,
        path=source / "revanent.yaml",
        config=default_config("report-fixture"),
        max_total_minutes_source=ConfigurationSource.DEFAULT,
    )
    report_service = EvidenceReportService(
        ReportComposition(
            status=StatusComposition(
                runs=repository,
                telemetry=TelemetryService(repository),
                git=git,
                repository_root=source,
            ),
            effective=effective,
            clock=lambda: datetime(2026, 8, 1, tzinfo=UTC),
        )
    )
    before = (
        repository.get_run(RUN_ID),
        repository.list_events(RUN_ID),
        repository.list_orchestration_records(RUN_ID),
        repository.list_usage_records(RUN_ID),
        repository.list_reservations(RUN_ID),
    )

    report = report_service.generate(EvidenceReportRequest(run_id=RUN_ID))

    assert report.status is EvidenceReportStatus.COMPLETE
    assert report.run_state is RunState.APPROVED
    assert report.evidence_complete is True
    assert report.verification.approval_permitted is True
    assert report.validation.status is not None
    assert report.review.approval_gate_valid is True
    assert before == (
        repository.get_run(RUN_ID),
        repository.list_events(RUN_ID),
        repository.list_orchestration_records(RUN_ID),
        repository.list_usage_records(RUN_ID),
        repository.list_reservations(RUN_ID),
    )


def test_active_run_report_is_incomplete(tmp_path: Path) -> None:
    _, request, repository, git, *_ = _harness(
        tmp_path,
        validation_statuses=(CommandStatus.SUCCESS,),
        review_results=(_approved_review(),),
        bind_runtime=True,
    )
    source = request.worktree.source_path
    report = EvidenceReportService(
        ReportComposition(
            status=StatusComposition(
                runs=repository,
                telemetry=TelemetryService(repository),
                git=git,
                repository_root=source,
            ),
            effective=EffectiveConfiguration(
                repository_root=source,
                path=source / "revanent.yaml",
                config=default_config("report-fixture"),
                max_total_minutes_source=ConfigurationSource.DEFAULT,
            ),
            clock=lambda: NOW,
        )
    ).generate(EvidenceReportRequest(run_id=RUN_ID))

    assert report.status is EvidenceReportStatus.INCOMPLETE
    assert report.evidence_complete is False
