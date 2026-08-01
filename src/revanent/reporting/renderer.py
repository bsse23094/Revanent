"""Pure canonical JSON and Markdown renderers for an evidence report."""

from __future__ import annotations

import json
import re

from revanent.ports.reporting import MAX_REPORT_BYTES, EvidenceReport, ReportFormat

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


class ReportRenderError(ValueError):
    """A report cannot be represented within its public bounded format."""


class ReportRenderer:
    """Render one canonical report object without inspecting external state."""

    def render(self, report: EvidenceReport, format_: ReportFormat) -> bytes:
        if format_ is ReportFormat.JSON:
            return self.json(report)
        return self.markdown(report)

    def json(self, report: EvidenceReport) -> bytes:
        value = (
            json.dumps(
                report.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            + b"\n"
        )
        self._bounded(value)
        return value

    def markdown(self, report: EvidenceReport) -> bytes:
        lines = [
            "# Revanent evidence report",
            "",
            f"- Report: `{report.report_id}`",
            f"- Status: `{report.status.value}`",
            f"- Run: `{report.run_id.root if report.run_id else 'unavailable'}`",
            f"- Evidence complete: `{str(report.evidence_complete).lower()}`",
            f"- Contradictory evidence: `{str(report.contradictory_evidence).lower()}`",
            "",
            "## Run",
            "",
            f"- State: `{report.run_state.value if report.run_state else 'unavailable'}`",
            f"- Revision: `{report.revision if report.revision is not None else 'unavailable'}`",
            f"- Reason: `{report.terminal_reason_code}`",
            f"- Cancellation terminal: `{str(report.cancellation_terminal).lower()}`",
            "",
            "## Verification",
            "",
            f"- Validation: `{_validation_status(report)}`",
            f"- Review: `{report.verification.review_decision or 'unavailable'}`",
            f"- ApprovalGate present: `{str(report.verification.approval_gate_present).lower()}`",
            f"- Approval permitted: `{str(report.verification.approval_permitted).lower()}`",
            "",
            "## Evidence sections",
            "",
        ]
        for section in report.sections:
            reasons = ", ".join(f"`{value}`" for value in section.reason_codes) or "none"
            lines.append(
                f"- `{section.name}`: complete=`{str(section.complete).lower()}`; reasons={reasons}"
            )
        lines.extend(
            [
                "",
                "## Attempts",
                "",
                "| Kind | Sequence | Status | Side effects |",
                "|---|---:|---|---|",
            ]
        )
        for attempt in report.attempts:
            lines.append(
                "| "
                + " | ".join(
                    (
                        self._inline(attempt.kind.value),
                        str(attempt.sequence),
                        self._inline(attempt.status.value),
                        self._inline(attempt.side_effects.value),
                    )
                )
                + " |"
            )
        lines.extend(
            [
                "",
                "## Validation",
                "",
                "| Command | Status | Duration ms |",
                "|---|---|---:|",
            ]
        )
        for command in report.validation.commands:
            lines.append(
                "| "
                f"`{self._inline(command.command_id)}` | "
                f"`{command.status.value}` | {command.duration_ms} |"
            )
        lines.extend(["", "## Review findings", ""])
        if report.review.findings:
            for finding in report.review.findings:
                lines.append(
                    f"- `{self._inline(finding.severity)}` `{self._inline(finding.finding_id)}`: "
                    f"{self._text(finding.summary)}"
                )
        else:
            lines.append("- None recorded.")
        lines.extend(["", "## Reproduction", ""])
        reproduction = report.reproduction
        lines.extend(
            (
                f"- Configuration schema: `{reproduction.configuration_schema_version}`",
                f"- Configuration digest: `{reproduction.configuration_digest_sha256}`",
                f"- Validation plan: `{reproduction.validation_plan_id or 'unavailable'}`",
                f"- Platform: `{self._inline(reproduction.platform)}`",
                f"- Python: `{self._inline(reproduction.python_version)}`",
                f"- Git: `{self._inline(reproduction.git_version)}`",
                f"- uv: `{self._inline(reproduction.uv_version)}`",
                "",
                "## Limitations",
                "",
            )
        )
        for limitation in report.limitations:
            lines.append(f"- `{self._inline(limitation)}`")
        if report.failure is not None:
            lines.extend(["", "## Report failure", "", f"- Code: `{report.failure.code}`"])
        value = ("\n".join(lines) + "\n").encode("utf-8")
        self._bounded(value)
        return value

    @staticmethod
    def _bounded(value: bytes) -> None:
        if len(value) > MAX_REPORT_BYTES:
            raise ReportRenderError("report representation exceeds the byte limit")

    @staticmethod
    def _inline(value: str) -> str:
        return _CONTROL.sub("?", value).replace("`", "\\`").replace("|", "\\|")

    @staticmethod
    def _text(value: str) -> str:
        return ReportRenderer._inline(value).replace("<", "&lt;").replace(">", "&gt;")


def _validation_status(report: EvidenceReport) -> str:
    value = report.verification.validation_status
    return value.value if value is not None else "unavailable"
