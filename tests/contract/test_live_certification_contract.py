from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from revanent.certification import LiveCertificationAuthorization, LiveCertificationRole


def _authorization(role: LiveCertificationRole) -> LiveCertificationAuthorization:
    return LiveCertificationAuthorization(
        role=role,
        provider="opencode" if role is LiveCertificationRole.OPENCODE_BUILDER else "codex",
        model="explicit-model",
        maximum_invocations=1,
        timeout_seconds=120,
        remote_token_ceiling=8_000,
        estimated_cost_ceiling_usd=Decimal("1.00"),
        network_authorized=True,
        write_authorized=role is not LiveCertificationRole.CODEX_REVIEWER,
        acknowledgement="I_AUTHORIZE_BOUNDED_LIVE_PROVIDER_CERTIFICATION",
    )


@pytest.mark.parametrize("role", tuple(LiveCertificationRole))
def test_live_authorization_is_role_scoped_finite_and_immutable(
    role: LiveCertificationRole,
) -> None:
    authorization = _authorization(role)

    assert authorization.maximum_invocations == 1
    assert authorization.remote_token_ceiling == 8_000
    with pytest.raises(ValidationError):
        authorization.write_authorized = not authorization.write_authorized


def test_reviewer_authorization_cannot_grant_repair_writes() -> None:
    values = _authorization(LiveCertificationRole.CODEX_REVIEWER).model_dump()
    values["write_authorized"] = True

    with pytest.raises(ValidationError):
        LiveCertificationAuthorization.model_validate(values)


def test_provider_substitution_and_unbounded_calls_are_rejected() -> None:
    values = _authorization(LiveCertificationRole.OPENCODE_BUILDER).model_dump()
    values["provider"] = "codex"
    with pytest.raises(ValidationError):
        LiveCertificationAuthorization.model_validate(values)

    values = _authorization(LiveCertificationRole.CODEX_REPAIRER).model_dump()
    values["maximum_invocations"] = 4
    with pytest.raises(ValidationError):
        LiveCertificationAuthorization.model_validate(values)
