# P4-001: Validation and Review Gates

- **Status:** PLANNED
- **Objective:** Make deterministic validation and structured review necessary for approval.
- **Requirements:** FR-007, FR-008, FR-015, NFR-004, OPS-004.
- **Dependencies:** P2-001, P3-001.
- **In scope:** validation plan/runner service, review schema v1, finding IDs/severity, parse errors,
  local approval-gate computation, JSON artifacts, unit/contract/integration tests.
- **Out of scope:** repair selection and full orchestration loop.
- **Steps:** define schemas; execute ordered required checks; parse review strictly; compute gates;
  test failed/missing/skipped evidence, high findings, scope drift, inconsistent locks, malformed prose.
- **Security constraints:** model cannot self-authorize; validation evidence immutable and bounded.
- **Acceptance criteria:** no failing/missing validation can approve; review contract matches documented
  v1 schema; all negative gates fail explicitly.
- **Verification:** canonical gates plus `uv run pytest tests/contract tests/integration -k 'validation or review'`.
- **Completion evidence:** Not started.
- **Risks:** conflating reviewer verdict with final system approval.
- **Recommended model/effort:** GPT-5.6 Sol, high.
- **Next package:** P4-002 — Bounded Orchestration and Repair.
