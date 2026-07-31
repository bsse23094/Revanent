# P4-002: Bounded Orchestration and Repair

- **Status:** COMPLETE (2026-07-31)
- **Objective:** Complete a resumable fake-provider build/validate/review/repair loop.
- **Requirements:** FR-009, FR-010, FR-011, FR-015, OPS-001, OPS-002, OPS-005.
- **Dependencies:** P1-002, P2-002, P3-001, P4-001.
- **In scope:** orchestration service, attempt/total budgets, cancellation, repair policy/reasons,
  idempotency boundaries, rereview, final run result, integration/E2E fake scenarios.
- **Out of scope:** context optimization, full user CLI, live providers.
- **Steps:** compose ports via state machine; implement counters/deadlines; repair classification;
  persist before/after effects; test success, both repairs, repetition, exhaustion, crash/resume.
- **Security constraints:** scope checked after each edit; all code changes force complete
  revalidation.
- **Acceptance criteria:** fake E2E exit cases pass; invalid approval impossible; limits exact;
  resume does not duplicate completed effects; every repair reason is durable.
- **Verification:** canonical gates plus `uv run pytest tests/integration tests/e2e`.
- **Completion evidence:** Provider-neutral strict version-1 orchestration contracts,
  append-only SQLite attempt journal migration, finite coordinator, deterministic repair
  policy, explicit reconciliation, and fake-first end-to-end approval/local-repair/Codex-
  repair paths are implemented. Durable intent precedes every worktree, agent, validation,
  and review side effect. Stable IDs plus revision guards prevent duplicate initiation;
  completed outcomes are reused after simulated crashes, while missing mutating outcomes
  are reconciled or blocked and never replayed. Required validation and the local
  `ReviewGate` remain mandatory. Attempt/duration limits, cancellation, worktree identity,
  scope, explicit Codex write authority, and terminal-state mappings are tested. Final
  audit additionally proves that in-flight reviewer cancellation reaches `CANCELLED` and
  that live ownership from another run is `INCOMPATIBLE` during reconciliation. Final
  canonical verification: 537 passed, one genuine Windows filename skip; no live provider,
  network, cleanup, destructive Git, commit, push, or merge operation occurred.
- **Risks:** durable intent cannot prove whether a process launched before a host crash;
  ambiguous mutating attempts require later human/Phase-6 recovery.
- **Recommended model/effort:** GPT-5.6 Sol, xhigh.
- **Next package:** P5-001 - Deterministic Context Selection and Manifest.
