# P1-002: Durable Run State and Events

- **Status:** COMPLETE — 2026-07-30
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
- **Completion evidence:** Implemented a provider-independent repository port,
  versioned `RunEvent`, SQLite schema/migration version 1, revisioned run snapshots,
  append-only sequenced events, stable event idempotency keys, short-lived verified
  connections, canonical validated reload, and explicit missing/duplicate/stale/
  incompatible/corrupt errors. Initial run creation is revision zero with no event.
  Thirty-five focused tests use real temporary SQLite files and prove initialization,
  compatibility rejection, persistence, complete round trips, deterministic ordering,
  append-only triggers, atomic commit/rollback, optimistic concurrency, idempotency,
  reopen recovery, corruption handling, foreign keys, and Windows-compatible paths.
  No speculative artifact rows/files were added because P1-001 defines no artifact
  reference type. In-flight marker reconciliation remains later orchestration work
  because no side-effect attempt contract exists; deterministic close/reopen reload is
  proved without claiming complete crash recovery.
  `uv run pytest tests/integration -k storage` passed all 29 integration tests.
  Full verification passed 119 tests; Ruff format checked 68 files; Ruff lint passed;
  strict mypy found no issues in 29 files; doctor and `git diff --check` passed.
- **Risks:** Multi-process behavior beyond SQLite writer serialization and revision
  rejection is unclaimed; in-flight side-effect reconciliation awaits orchestration.
- **Recommended model/effort:** GPT-5.6 Sol, high.
- **Next package:** P2-001 — Controlled Commands and Path Policy.
