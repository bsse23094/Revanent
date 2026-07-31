# P3-002: OpenCode and Codex Adapters

- **Status:** COMPLETE (2026-07-30)
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
- **Completion evidence:** Version/help-only detection maps OpenCode absence to an
  actionable typed result and accepts the installed `codex-cli 0.146.0-alpha.3.1`
  read-only/workspace-write `exec` surface. OpenCode builder and separate Codex reviewer/
  repairer adapters execute exclusively through `CommandRunner`, with deterministic
  prompts, exact arguments, strict JSONL plus P3-001 parsing, explicit repair authority,
  minimal typed environment overrides, bounded output, and normalized failures. Finite
  fake executables verify all three roles without provider credentials, network, or model
  calls. Final verification: focused 42 passed; full 373 passed in 148.14 seconds with
  one genuine Windows-only skip; canonical format, Ruff, mypy, doctor, diff check, and
  security scans passed.
- **Risks:** provider CLI drift fails closed; OpenCode's compatible surface is fake-only
  locally because the executable is absent. Provider sandbox flags are not OS guarantees.
- **Recommended model/effort:** GPT-5.6 Terra, high; Sol review for write/security boundaries.
- **Next package:** P4-001 — Validation and Review Gates.
