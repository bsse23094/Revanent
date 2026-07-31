# Changelog

All notable changes will be recorded here. The project follows Keep a Changelog
and will adopt Semantic Versioning when its first release is cut.

## [Unreleased]

### Added

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
