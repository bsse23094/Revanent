# P2-001: Controlled Commands and Path Policy

- **Status:** PLANNED
- **Objective:** Provide the sole bounded subprocess runner and normalized path/executable policy.
- **Requirements:** SEC-001, SEC-002, SEC-003, SEC-004, NFR-007.
- **Dependencies:** P1-001.
- **In scope:** argument-list runner, cwd/allowlist policy, environment filter, redaction, timeout,
  cancellation, bounded capture/artifacts, Windows/POSIX path policy and fake executables.
- **Out of scope:** Git worktrees and provider-specific command construction.
- **Steps:** define port/results; implement policies; add adversarial tests for injection, traversal,
  links/junctions, case, secrets, timeout, cancellation, and output truncation; document trust limits.
- **Security constraints:** no shell by default; fail closed; executable and cwd must be authorized.
- **Acceptance criteria:** malicious fixtures cannot escape policy; secrets are redacted; children stop;
  full output is bounded/referenced; platform integration tests pass.
- **Verification:** canonical gates plus `uv run pytest tests/unit tests/integration -k command`.
- **Completion evidence:** Not started.
- **Risks:** Windows process-tree cancellation and junction behavior.
- **Recommended model/effort:** GPT-5.6 Sol, xhigh.
- **Next package:** P2-002 — Safe Git Worktrees.
