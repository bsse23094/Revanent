"""Application boundary for report rendering and explicitly requested artifact output."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from revanent.application.reports import EvidenceReportService
from revanent.config import EffectiveConfiguration, resolve_project_paths
from revanent.ports.reporting import (
    EvidenceReport,
    EvidenceReportManifest,
    EvidenceReportRequest,
    EvidenceReportStatus,
    ReportArtifactWriteResult,
    ReportFormat,
)
from revanent.reporting import LocalReportArtifactWriter, ReportRenderer
from revanent.reporting.writer import ReportArtifactWriteError


class _CommandModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, str_strip_whitespace=True)


class ReportCommandRequest(_CommandModel):
    run_id: str = Field(pattern=r"^run_[0-9a-f]{32}$")
    format: ReportFormat = ReportFormat.MARKDOWN
    output: str | None = Field(default=None, min_length=1, max_length=512)


class ReportCommandResult(_CommandModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, str_strip_whitespace=False)

    status: EvidenceReportStatus
    report: EvidenceReport
    content: str
    artifact: ReportArtifactWriteResult | None = None
    manifest: EvidenceReportManifest | None = None


@dataclass(frozen=True, slots=True)
class ReportCommandComposition:
    report_service: EvidenceReportService
    effective: EffectiveConfiguration
    renderer: ReportRenderer
    writer: LocalReportArtifactWriter


class ReportCommandService:
    """Generate one read-only report and optionally no-clobber-write its rendered bytes."""

    def __init__(self, composition: ReportCommandComposition) -> None:
        self._composition = composition

    def generate(self, request: ReportCommandRequest) -> ReportCommandResult:
        from revanent.domain import RunId

        report = self._composition.report_service.generate(
            EvidenceReportRequest(run_id=RunId(request.run_id))
        )
        data = self._composition.renderer.render(report, request.format)
        artifact = None
        manifest = None
        status = report.status
        if request.output is not None:
            try:
                artifact = self._composition.writer.write(
                    root=resolve_project_paths(self._composition.effective).report_root,
                    relative_path=request.output,
                    data=data,
                    content_type=(
                        "application/json"
                        if request.format is ReportFormat.JSON
                        else "text/markdown"
                    ),
                    correlation=report.report_id,
                )
                manifest = EvidenceReportManifest(
                    report_id=report.report_id,
                    format=request.format,
                    source_revision=report.revision,
                    generated_at=report.generated_at,
                    artifact_reference=artifact.artifact.reference,
                    content_bytes=artifact.artifact.stored_bytes,
                    content_sha256=artifact.artifact.digest_sha256 or "0" * 64,
                    evidence_complete=report.evidence_complete,
                )
            except ReportArtifactWriteError:
                status = EvidenceReportStatus.OUTPUT_CONFLICT
        return ReportCommandResult(
            status=status,
            report=report,
            content=data.decode("utf-8"),
            artifact=artifact,
            manifest=manifest,
        )
