from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import pytest

from revanent.certification import LiveCertificationAuthorization, LiveCertificationRole


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("revanent-live")
    group.addoption("--live-certify", action="store_true", help="Authorize selected live tests.")
    group.addoption("--live-opencode-model")
    group.addoption("--live-codex-review-model")
    group.addoption("--live-codex-repair-model")
    group.addoption("--live-timeout-seconds", type=int, default=180)
    group.addoption("--live-token-ceiling", type=int, default=32_000)
    group.addoption("--live-cost-ceiling-usd", default="5.00")
    group.addoption(
        "--live-acknowledgement",
        help="Exact bounded-live-certification acknowledgement.",
    )


def live_authorization(
    config: pytest.Config,
    role: LiveCertificationRole,
) -> LiveCertificationAuthorization:
    if not config.getoption("--live-certify"):
        pytest.fail("live certification requires --live-certify")
    option = {
        LiveCertificationRole.OPENCODE_BUILDER: "--live-opencode-model",
        LiveCertificationRole.CODEX_REVIEWER: "--live-codex-review-model",
        LiveCertificationRole.CODEX_REPAIRER: "--live-codex-repair-model",
    }[role]
    model = config.getoption(option)
    if not model:
        pytest.fail(f"live certification requires an explicit {option} value")
    return LiveCertificationAuthorization(
        role=role,
        provider=("opencode" if role is LiveCertificationRole.OPENCODE_BUILDER else "codex"),
        model=model,
        maximum_invocations=1,
        timeout_seconds=config.getoption("--live-timeout-seconds"),
        remote_token_ceiling=config.getoption("--live-token-ceiling"),
        estimated_cost_ceiling_usd=Decimal(config.getoption("--live-cost-ceiling-usd")),
        network_authorized=True,
        write_authorized=role is not LiveCertificationRole.CODEX_REVIEWER,
        acknowledgement=config.getoption("--live-acknowledgement"),
    )


@pytest.fixture
def live_authorizer(
    pytestconfig: pytest.Config,
) -> Callable[[LiveCertificationRole], LiveCertificationAuthorization]:
    return lambda role: live_authorization(pytestconfig, role)
