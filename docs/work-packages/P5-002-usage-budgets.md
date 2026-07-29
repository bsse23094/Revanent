# P5-002: Usage and Budget Telemetry

- **Status:** PLANNED
- **Objective:** Track provenance-labeled usage and enforce time/token/cost/context budgets.
- **Requirements:** FR-010, NFR-008, OPS-001, OPS-005.
- **Dependencies:** P5-001.
- **In scope:** usage records/aggregation, measured/reported/estimated/unavailable provenance,
  deadline and remote budget checks, local/remote attempt counts, persistence/report inputs.
- **Out of scope:** billing, provider pricing service, claimed savings.
- **Steps:** define units/provenance; implement aggregation and pre/post-attempt checks; test null,
  partial, cached, reasoning, rounding, and exhaustion; document pricing snapshot handling.
- **Security constraints:** usage metadata cannot contain prompts/secrets; fail before costly attempt.
- **Acceptance criteria:** accounting deterministic; estimates labeled; exhaustion stops predictably;
  no token/cost claim lacks provenance.
- **Verification:** canonical gates plus `uv run pytest tests/unit tests/contract -k 'usage or budget'`.
- **Completion evidence:** Not started.
- **Risks:** inconsistent provider telemetry and volatile pricing.
- **Recommended model/effort:** GPT-5.6 Terra, medium.
- **Next package:** P6-001 — Initialization and Configuration CLI.
