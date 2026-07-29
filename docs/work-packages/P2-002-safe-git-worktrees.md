# P2-002: Safe Git Worktrees

- **Status:** PLANNED
- **Objective:** Inspect repositories and manage only Revanent-owned isolated worktrees safely.
- **Requirements:** FR-002, FR-004, SEC-005, OPS-002, OPS-003.
- **Dependencies:** P1-002, P2-001.
- **In scope:** Git port/adapter, dirty-state snapshot, protected-branch policy, naming/ownership,
  create/verify/preserve/clean worktrees, diff/base evidence, temporary-repository tests.
- **Out of scope:** commits, pushes, merges, provider calls, orchestration loop.
- **Steps:** parse Git capabilities/status; implement non-force lifecycle; record ownership/base;
  test dirt, untracked work, collisions, interruption, non-owned cleanup refusal, Windows paths.
- **Security constraints:** no force/reset/push/merge; verify absolute owned targets before cleanup.
- **Acceptance criteria:** user changes remain untouched; only owned worktrees are removable; state is
  auditable and resumable; integration tests pass on supported platforms.
- **Verification:** canonical gates plus `uv run pytest tests/integration -k git`.
- **Completion evidence:** Not started.
- **Risks:** Git version output and locked Windows directories.
- **Recommended model/effort:** GPT-5.6 Sol, xhigh.
- **Next package:** P3-001 — Agent Contracts and Fake Adapter.
