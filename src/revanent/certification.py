"""Strict, non-secret authorization and evidence contracts for opt-in live certification."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class LiveCertificationRole(StrEnum):
    OPENCODE_BUILDER = "OPENCODE_BUILDER"
    CODEX_REVIEWER = "CODEX_REVIEWER"
    CODEX_REPAIRER = "CODEX_REPAIRER"


class _CertificationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, str_strip_whitespace=True)


class LiveCertificationAuthorization(_CertificationModel):
    """One role-scoped, finite authorization; it grants no Git publication authority."""

    schema_version: Literal[1] = 1
    role: LiveCertificationRole
    provider: Literal["opencode", "codex"]
    model: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/+@-]{0,127}$")
    maximum_invocations: int = Field(ge=1, le=3)
    timeout_seconds: int = Field(ge=1, le=600)
    remote_token_ceiling: int = Field(ge=1, le=1_000_000)
    estimated_cost_ceiling_usd: Decimal = Field(gt=0, le=Decimal("100"))
    network_authorized: Literal[True]
    write_authorized: bool
    acknowledgement: Literal["I_AUTHORIZE_BOUNDED_LIVE_PROVIDER_CERTIFICATION"]

    @model_validator(mode="after")
    def _role_authority(self) -> Self:
        expected = "opencode" if self.role is LiveCertificationRole.OPENCODE_BUILDER else "codex"
        if self.provider != expected:
            raise ValueError("live certification role and provider do not match")
        writes = self.role is not LiveCertificationRole.CODEX_REVIEWER
        if self.write_authorized != writes:
            raise ValueError("live certification write authority does not match the role")
        return self


class LiveCertificationEvidence(_CertificationModel):
    """Bounded metadata-only certification evidence; digests are not signatures."""

    schema_version: Literal[1] = 1
    scenario_id: str = Field(pattern=r"^live_[a-z0-9_.-]{1,63}$")
    role: LiveCertificationRole
    provider: Literal["opencode", "codex"]
    model: str = Field(min_length=1, max_length=128)
    provider_version: str = Field(min_length=1, max_length=128)
    platform: str = Field(min_length=1, max_length=256)
    python_version: str = Field(min_length=1, max_length=128)
    generated_at: datetime
    invocation_count: int = Field(ge=0, le=3)
    validation_status: str = Field(min_length=1, max_length=64)
    review_status: str = Field(min_length=1, max_length=64)
    repair_status: str = Field(min_length=1, max_length=64)
    telemetry_provenance: str = Field(min_length=1, max_length=64)
    report_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    repository_id: str = Field(min_length=1, max_length=128)
    worktree_id: str = Field(min_length=1, max_length=128)
    publication_performed: Literal[False] = False
    limitations: tuple[str, ...] = Field(default=(), max_length=32)

    @field_validator("generated_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("certification timestamps must be UTC")
        return value
