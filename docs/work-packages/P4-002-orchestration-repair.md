# P4-002: Bounded Orchestration and Repair

- **Status:** PLANNED
- **Objective:** Complete a resumable fake-provider build/validate/review/repair loop.
- **Requirements:** FR-009, FR-010, FR-011, FR-015, OPS-001, OPS-002, OPS-005.
- **Dependencies:** P1-002, P2-002, P3-001, P4-001.
- **In scope:** orchestration service, attempt/total budgets, cancellation, repair policy/reasons,
  idempotency boundaries, rereview, final run result, integration/E2E fake scenarios.
- **Out of scope:** context optimization, full user CLI, live providers.
- **Steps:** compose ports via state machine; implement counters/deadlines; repair classification;
  persist before/after effects; test success, both repairs, repetition, exhaustion, crash/resume.
- **Security constraints:** scope checked after each edit; all code changes force complete revalidation.
- **Acceptance criteria:** fake E2E exit cases pass; invalid approval impossible; limits exact; resume
  does not duplicate completed effects; every repair reason is durable.
- **Verification:** canonical gates plus `uv run pytest tests/integration tests/e2e`.
- **Completion evidence:** Not started.
- **Risks:** subtle crash windows and off-by-one attempt semantics.
- **Recommended model/effort:** GPT-5.6 Sol, xhigh.
- **Next package:** P5-001 — Deterministic Context Packages.
