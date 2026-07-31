# P1-001: Domain, Configuration, and State Machine

- **Status:** COMPLETE — 2026-07-30
- **Objective:** Freeze versioned typed boundaries and central transition invariants.
- **Requirements:** FR-001, FR-008, FR-010, FR-015, NFR-002, NFR-004, OPS-006, OPS-007.
- **Dependencies:** P0-001.
- **In scope:** Pydantic config, IDs and core run/task/budget/review schemas, transition function,
  schema serialization contracts, example config alignment, unit/contract tests.
- **Out of scope:** SQLite, subprocess execution, Git, provider adapters, orchestration side effects.
- **Steps:** inventory schemas; define strict/versioned models; implement transition table/errors;
  validate cross-field limits and paths; add positive/negative and round-trip tests; document.
- **Security constraints:** reject unknown schema versions/unsafe bounds; do not store secrets;
  terminal and approval invariants cannot be bypassed by deserialization.
- **Acceptance criteria:** invalid transitions/config fail explicitly; schemas round-trip
  deterministically; review approval gates have domain representation; all gates pass.
- **Verification:** `uv run ruff format --check .`; `uv run ruff check .`;
  `uv run mypy src tests`; `uv run pytest tests/unit tests/contract`.
- **Completion evidence:** Implemented immutable version-1 configuration/domain models,
  canonical IDs and wire enums, the complete centralized transition table, typed
  transition/approval errors, local approval evidence, safe YAML loading, deterministic
  round trips, and bypass-resistant run updates. Added ADR-0003 and 76 focused tests.
  `uv sync --dev` resolved/audited 27 packages; Ruff format reported 61 files formatted;
  Ruff lint passed; strict mypy reported no issues in 22 source files; the full suite
  passed 84 tests; focused P1-001 tests passed 76 tests; `revanent doctor` passed with
  required tools available, Codex available, and OpenCode accurately unavailable;
  `git diff --check` passed.
- **Risks:** premature schema breadth; unsafe config precedence assumptions.
- **Recommended model/effort:** GPT-5.6 Sol, high.
- **Next package:** P1-002 — Durable Run State and Events.
