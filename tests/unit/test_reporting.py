from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from revanent.ports.reporting import (
    EvidenceReport,
    EvidenceReportStatus,
    ReportFinding,
    ReportReview,
    ReproductionEvidence,
    VerificationEvidence,
)
from revanent.reporting import LocalReportArtifactWriter, ReportRenderer
from revanent.reporting.writer import ReportArtifactWriteError

NOW = datetime(2026, 8, 1, 12, tzinfo=UTC)


def _report(**changes: object) -> EvidenceReport:
    values: dict[str, object] = {
        "report_id": "report_" + "a" * 64,
        "status": EvidenceReportStatus.COMPLETE,
        "generated_at": NOW,
        "generator_version": "0.1.0",
        "evidence_complete": True,
        "reproduction": ReproductionEvidence(
            configuration_schema_version=1,
            configuration_digest_sha256="b" * 64,
            platform="Windows",
            python_version="3.12.0",
        ),
        "verification": VerificationEvidence(
            approval_gate_present=True,
            approval_permitted=True,
        ),
    }
    values.update(changes)
    return EvidenceReport.model_validate(values)


def test_report_contract_is_strict_immutable_and_schema_versioned() -> None:
    report = _report()
    serialized = report.model_dump(mode="json")

    assert EvidenceReport.model_validate_json(report.model_dump_json()) == report
    with pytest.raises(ValidationError):
        EvidenceReport.model_validate({**serialized, "unexpected": "field"})
    with pytest.raises(ValidationError):
        EvidenceReport.model_validate({**serialized, "schema_version": 2})
    with pytest.raises(ValidationError):
        report.status = EvidenceReportStatus.BLOCKED


def test_json_is_canonical_and_markdown_escapes_untrusted_text() -> None:
    report = _report(
        review=ReportReview(
            approval_gate_present=True,
            approval_gate_valid=True,
            unresolved_high_or_critical=0,
            findings=(
                ReportFinding(
                    finding_id="finding-1",
                    severity="HIGH",
                    summary="<script>\x1b[31msecret</script> | `code`",
                ),
            ),
        )
    )
    renderer = ReportRenderer()

    first = renderer.json(report)
    second = renderer.json(EvidenceReport.model_validate_json(first))
    markdown = renderer.markdown(report).decode("utf-8")

    assert first == second
    assert first.endswith(b"\n")
    assert "<script>" not in markdown
    assert "\x1b" not in markdown
    assert "&lt;script&gt;" in markdown
    assert "\\|" in markdown


def test_artifact_writer_is_root_bound_idempotent_and_no_clobber(tmp_path: Path) -> None:
    root = tmp_path / "reports"
    root.mkdir()
    writer = LocalReportArtifactWriter()

    initial = writer.write(
        root=root,
        relative_path="runs/report.json",
        data=b"{}\n",
        content_type="application/json",
        correlation="report_" + "a" * 64,
    )
    repeat = writer.write(
        root=root,
        relative_path="runs/report.json",
        data=b"{}\n",
        content_type="application/json",
        correlation="report_" + "a" * 64,
    )

    assert initial.created is True
    assert repeat.created is False
    assert initial.artifact.digest_sha256 == repeat.artifact.digest_sha256
    with pytest.raises(ReportArtifactWriteError):
        writer.write(
            root=root,
            relative_path="runs/report.json",
            data=b'{"different":true}\n',
            content_type="application/json",
            correlation="report_" + "a" * 64,
        )


@pytest.mark.parametrize("name", ("../escape.json", ".git/report.json"))
def test_artifact_writer_refuses_unsafe_names(tmp_path: Path, name: str) -> None:
    root = tmp_path / "reports"
    root.mkdir()

    with pytest.raises(ReportArtifactWriteError):
        LocalReportArtifactWriter().write(
            root=root,
            relative_path=name,
            data=b"{}\n",
            content_type="application/json",
            correlation="report_" + "a" * 64,
        )


def test_artifact_writer_refuses_an_absolute_name(tmp_path: Path) -> None:
    root = tmp_path / "reports"
    root.mkdir()

    with pytest.raises(ReportArtifactWriteError):
        LocalReportArtifactWriter().write(
            root=root,
            relative_path=str(tmp_path / "outside.json"),
            data=b"{}\n",
            content_type="application/json",
            correlation="report_" + "a" * 64,
        )


def test_concurrent_identical_writers_reuse_one_atomic_artifact(tmp_path: Path) -> None:
    root = tmp_path / "reports"
    root.mkdir()
    writer = LocalReportArtifactWriter()

    def write(_: int) -> bool:
        return writer.write(
            root=root,
            relative_path="same.json",
            data=b'{"stable":true}\n',
            content_type="application/json",
            correlation="report_" + "a" * 64,
        ).created

    with ThreadPoolExecutor(max_workers=4) as executor:
        created = tuple(executor.map(write, range(4)))

    assert created.count(True) == 1
    assert (root / "same.json").read_bytes() == b'{"stable":true}\n'


def test_concurrent_conflicting_writers_never_overwrite_the_winner(tmp_path: Path) -> None:
    root = tmp_path / "reports"
    root.mkdir()
    writer = LocalReportArtifactWriter()

    def write(data: bytes) -> bytes | str:
        try:
            writer.write(
                root=root,
                relative_path="race.json",
                data=data,
                content_type="application/json",
                correlation="report_" + "a" * 64,
            )
        except ReportArtifactWriteError:
            return "refused"
        return data

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(write, (b'{"one":1}\n', b'{"two":2}\n')))

    assert outcomes.count("refused") == 1
    assert (root / "race.json").read_bytes() in {b'{"one":1}\n', b'{"two":2}\n'}
