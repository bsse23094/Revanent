# P6-001: Initialization and Configuration CLI

- **Status:** COMPLETE â€” 2026-08-01
- **Objective:** Make safe setup, config validation, doctor, and provider detection user-ready.
- **Requirements:** FR-013, FR-014, OPS-004, OPS-006.
- **Dependencies:** P2-002, P3-002, P5-002.
- **In scope:** `init`, mature `doctor`, `config validate`, `agents detect`, config precedence/overrides,
  non-overwrite behavior, writable-state checks, CLI tests and docs.
- **Out of scope:** run/resume/report commands and destructive clean.
- **Steps:** connect typed services; implement idempotent init and exit codes; add concise Rich output;
  test non-Git/dirty/existing config/missing tools/invalid config; document commands.
- **Security constraints:** never overwrite without approval; sanitized effective config only.
- **Acceptance criteria:** a new repository initializes safely; errors are actionable; all doctor checks
  accurately label required/optional/simulated/unavailable states.
- **Verification:** canonical gates plus `uv run pytest tests/integration -k cli` and CLI smoke commands.
- **Completion evidence:** Typed application services and a presentation-only Typer surface now
  implement `init`, `doctor`, `config validate`, and `agents detect`. Initialization is
  deterministic/no-clobber and reuses compatible resources; configuration is root-bound and
  schema-v1 validated; doctor and detection are read-only P3 capability probes. Unit, contract,
  temporary-real-Git integration, adversarial, static, and full-suite verification are recorded
  in `PROJECT_STATE.md`.
- **Risks:** interactive behavior and Windows console differences.
- **Recommended model/effort:** GPT-5.6 Terra, high.
- **Next package:** P6-002 — Run, Resume, Status, and Reporting CLI.
