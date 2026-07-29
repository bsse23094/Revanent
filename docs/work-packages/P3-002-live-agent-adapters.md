# P3-002: OpenCode and Codex Adapters

- **Status:** PLANNED
- **Objective:** Detect and invoke initial provider CLIs without undocumented assumptions.
- **Requirements:** FR-006, FR-014, SEC-002, SEC-006, SEC-007, OPS-004, OPS-008.
- **Dependencies:** P3-001.
- **In scope:** version/capability discovery, command builders, OpenCode builder and Codex
  review/repair modes, output capture/parsing, fake-executable contract tests, blockers.
- **Out of scope:** default paid/live calls and orchestration policy.
- **Steps:** verify official CLI help/version surfaces at implementation time; isolate flags;
  implement adapters through controlled runner; test absence, exit, timeout, malformed output.
- **Security constraints:** review mode read-only; repair explicitly write-enabled; filtered context/env.
- **Acceptance criteria:** fake executables cover every normalized outcome; missing providers yield
  actionable blockers; live tests remain opt-in and labeled.
- **Verification:** canonical gates plus `uv run pytest tests/contract tests/integration -k adapter`.
- **Completion evidence:** Not started; OpenCode currently unavailable.
- **Risks:** provider CLI drift and structured-output capability variance.
- **Recommended model/effort:** GPT-5.6 Terra, high; Sol review for write/security boundaries.
- **Next package:** P4-001 — Validation and Review Gates.
