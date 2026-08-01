# Changelog

All notable changes will be recorded here. The project follows Keep a Changelog
and will adopt Semantic Versioning when its first release is cut.

## [Unreleased]

### Added

- Began P7-001-C1 with default-off, role-scoped, finite live certification contracts and pytest
  markers/options; disposable source/owned-worktree scenarios; separate OpenCode builder, Codex
  reviewer, and Codex repair authorization; and metadata-only certificate contracts.
- Hardened runtime composition so provider stdin is enabled only after explicit network, builder,
  and reviewer authorization. Live prompts now include trusted adapter identity and the exact
  response schema; Codex JSONL accepts bounded interim messages and selects the final message.

- Completed P6-002-C2 and Phase 6: strict immutable schema-v1 evidence reports, read-only durable
  assembly shared with status facts, canonical JSON, deterministic escaped Markdown, reproduction/
  limitation evidence, typed report outcomes, and `revanent report`.
- Explicit report artifacts are report-root-relative, bounded, SHA-256-labelled integrity metadata
  (not signatures), atomic/no-clobber, and idempotently reuse identical bytes. ADR-0013 records the
  design; no report body is stored in SQLite and no migration was added.

- Completed P6-002-C1 typed `run`, `resume`, read-only `status`, and idempotent `cancel` workflows;
  SQLite migration 5 atomically persists immutable Run/repository/worktree bindings before every
  side effect; full typed identity and live owned-worktree checks fail closed before recovery.
- Versioned provider-neutral status projects bounded event/attempt/context/validation/review/usage/
  budget/cancellation/artifact evidence, detects contradictions as `INVALID_EVIDENCE`, and performs
  no reconciliation, settlement, transition, provider probe, or write.
- Bounded task JSON safety, provider-before-persistence validation, restart no-replay recovery,
  real-SQLite resume/resume and resume/cancel races, stable exits/output, and fake/local-only CLI
  E2E coverage. Report artifacts and cleanup remain absent for P6-002-C2.
- P6-001 safe `init`, root-bound `config validate`, read-only typed `doctor`, and P3-backed
  `agents detect` CLI workflows; no-clobber initialization plans, deterministic exit codes,
  filtered environment/provider probes, ADR-0012, and temporary-repository safety coverage.

- Phase 0 project charter, requirements, architecture, roadmap, ADRs, and work packages.
- Python package, Typer CLI, read-only environment doctor, CI, and foundation tests.
- P1-001 immutable versioned configuration/domain schemas, centralized run-state
  transitions, local approval gates, and deterministic serialization contracts.
- P1-002 SQLite schema migrations, revisioned run persistence, append-only sequenced
  events, atomic transition commits, optimistic concurrency, and validated reload.
- P2-001 provider-independent controlled-command contracts; executable, path,
  environment, resource, and redaction policies; shell-free local execution; bounded
  redacted overflow artifacts; timeout/cancellation cleanup; and adversarial tests.
- P2-002 provider-independent Git contracts; deterministic porcelain repository/status/
  worktree inspection; protected dedicated branches; exact-base worktree creation;
  atomic versioned ownership records; live ownership verification; conservative
  non-force cleanup with retained branches/evidence; partial/stale recovery refusal;
  and adversarial real-Git Windows/POSIX test coverage.
- P3-001 immutable versioned agent capability/request/response/failure/artifact
  contracts; exact invocation correlation; bounded strict JSON normalization with
  secret redaction; provider-neutral adapter port; and finite deterministic fake
  scenarios covering success, blockers, failures, malformed output, timeout,
  cancellation, unsupported capability, concurrency, and replay.
- P3-002 version/help-only OpenCode/Codex capability detection; controlled OpenCode
  builder and permission-separated Codex reviewer/repairer adapters; frozen safe
  arguments, bounded deterministic prompts, strict JSONL translation, typed command
  failure normalization, minimal environment/redaction policy, enhanced doctor output,
  and finite fake-executable integration without live provider calls.
- P4-001 immutable version-1 validation plans/results, deterministic ordered execution
  through the controlled-command port, typed failure/cancellation/advisory aggregation,
  safe bounded artifact evidence, strict structured reviewer correlation, deterministic
  local gate decisions, and local-only `ApprovalGate` construction with adversarial
  fake-command/fake-agent coverage.
- P4-002 strict version-1 orchestration attempts/results/repair/reconciliation contracts;
  SQLite schema migration 2 append-only orchestration journal; finite provider-neutral
  coordinator with stable intent/outcome boundaries, crash-safe outcome reuse, owned-
  worktree verification, cancellation, exact limits, and fail-closed reconciliation;
  deterministic local-builder/Codex repair escalation and explicit write authorization;
  mandatory post-mutation validation and local-only approval; and fake-first adversarial
  crash/restart, concurrency, limit, scope, ownership, and approval E2E coverage.
- P4-002 audit hardening for reviewer-cancellation precedence and exact live-worktree
  identity matching during interrupted workspace reconciliation.
- P5-001 deterministic context contracts, bounded multi-source discovery and selection,
  centralized race-aware reads, redaction/provenance/manifest evidence, agent-request context
  projection, durable orchestration context outcomes, SQLite migration 3, and adversarial
  local-only coverage.
- P5-002 immutable usage/provenance/budget/reservation/settlement contracts; SQLite migration 4;
  atomic attempt, duration, token, and Decimal-cost reservation/settlement; validation timeout
  capping and measured overage; restart reconciliation without replay; unavailable/unresolved
  semantics; metadata-only privacy; and real local SQLite concurrency/adversarial coverage.
