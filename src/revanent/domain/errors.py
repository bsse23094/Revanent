"""Explicit domain failures."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from revanent.domain.identifiers import RunId
    from revanent.domain.models import RunState


class DomainError(Exception):
    """Base class for failures caused by invalid domain operations."""


class InvalidIdentifierError(ValueError, DomainError):
    """A stable identifier did not match its canonical representation."""


class InvalidTransitionError(DomainError):
    """A requested run-state transition is not permitted."""

    def __init__(self, run_id: RunId, source: RunState, destination: RunState) -> None:
        self.run_id = run_id
        self.source = source
        self.destination = destination
        super().__init__(
            f"run {run_id.root} cannot transition from {source.value} to {destination.value}"
        )


class ApprovalGateError(DomainError):
    """Approval was requested without complete, passing local gate evidence."""

    def __init__(self, failed_gates: tuple[str, ...]) -> None:
        self.failed_gates = failed_gates
        detail = ", ".join(failed_gates) if failed_gates else "approval evidence missing"
        super().__init__(f"approval gates not satisfied: {detail}")
