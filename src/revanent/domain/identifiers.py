"""Stable, validated domain identifiers."""

from __future__ import annotations

import re
from typing import ClassVar, Self
from uuid import uuid4

from pydantic import RootModel, model_validator

from revanent.domain.errors import InvalidIdentifierError


class _StableIdentifier(RootModel[str]):
    """Immutable string identifier with a canonical representation."""

    pattern: ClassVar[re.Pattern[str]]
    description: ClassVar[str]

    model_config = {"frozen": True}

    @model_validator(mode="after")
    def _validate_root(self) -> Self:
        if self.pattern.fullmatch(self.root) is None:
            raise InvalidIdentifierError(f"invalid {self.description}: {self.root!r}")
        return self

    def __str__(self) -> str:
        return self.root


class RunId(_StableIdentifier):
    """Random stable identity for one run."""

    pattern = re.compile(r"run_[0-9a-f]{32}")
    description = "run identifier (expected run_ followed by 32 lowercase hex characters)"

    @classmethod
    def new(cls) -> RunId:
        """Create a new canonical run identifier."""
        return cls(f"run_{uuid4().hex}")


class TaskId(_StableIdentifier):
    """Random stable identity for one bounded task."""

    pattern = re.compile(r"task_[0-9a-f]{32}")
    description = "task identifier (expected task_ followed by 32 lowercase hex characters)"

    @classmethod
    def new(cls) -> TaskId:
        """Create a new canonical task identifier."""
        return cls(f"task_{uuid4().hex}")


class WorkPackageId(_StableIdentifier):
    """Human-readable stable identity for a work package."""

    pattern = re.compile(r"[A-Z][A-Z0-9]{0,15}-[0-9]{3}")
    description = "work-package identifier (expected an uppercase prefix and three digits)"


class EventId(_StableIdentifier):
    """Random stable idempotency identity for one run event."""

    pattern = re.compile(r"event_[0-9a-f]{32}")
    description = "event identifier (expected event_ followed by 32 lowercase hex characters)"

    @classmethod
    def new(cls) -> EventId:
        """Create a new canonical event identifier."""
        return cls(f"event_{uuid4().hex}")


class AgentInvocationId(_StableIdentifier):
    """Stable correlation identity for one provider-neutral agent invocation."""

    pattern = re.compile(r"inv_[0-9a-f]{32}")
    description = (
        "agent invocation identifier (expected inv_ followed by 32 lowercase hex characters)"
    )

    @classmethod
    def new(cls) -> AgentInvocationId:
        """Create a new canonical invocation identifier."""
        return cls(f"inv_{uuid4().hex}")


class AgentAttemptId(_StableIdentifier):
    """Stable identity for one build, review, or repair attempt."""

    pattern = re.compile(r"attempt_[0-9a-f]{32}")
    description = (
        "agent attempt identifier (expected attempt_ followed by 32 lowercase hex characters)"
    )

    @classmethod
    def new(cls) -> AgentAttemptId:
        """Create a new canonical attempt identifier."""
        return cls(f"attempt_{uuid4().hex}")
