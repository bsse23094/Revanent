# P1-002: Durable Run State and Events

- **Status:** PLANNED
- **Objective:** Persist/reload runs and append-only events transactionally with migrations.
- **Requirements:** FR-011, NFR-003, OPS-001, OPS-002, OPS-007.
- **Dependencies:** P1-001.
- **In scope:** storage port, SQLite adapter/schema/migrations, artifact references, reload/recovery tests.
- **Out of scope:** worktrees, providers, orchestration loop, CLI resume.
- **Steps:** design transaction boundary; implement v1 migration/repository; add idempotency keys;
  test event ordering, rollback, reload, incompatible schema, and interrupted marker reconciliation.
- **Security constraints:** parameterized SQL; bounded metadata; atomic writes; no secret/source duplication.
- **Acceptance criteria:** state/event commit is atomic; reload deterministic; migration mismatch explicit;
  restart tests pass and ADR-0002 implementation notes are current.
- **Verification:** canonical quality gates plus `uv run pytest tests/integration -k storage`.
- **Completion evidence:** Not started.
- **Risks:** SQLite locking and Windows file semantics.
- **Recommended model/effort:** GPT-5.6 Sol, high.
- **Next package:** P2-001 — Controlled Commands and Path Policy.
