# P5-002: Usage Telemetry and Budget Enforcement

- **Status:** COMPLETE — 2026-07-31
- **Objective:** Track provenance-labelled usage and enforce duration, attempt, remote-token,
  and estimated-cost budgets without confusing local bytes, provider tokens, or billing.
- **Requirements:** FR-010, NFR-008, OPS-001, OPS-005.
- **Dependencies:** P5-001.
- **Out of scope:** billing, current/provider pricing services, token-savings claims, distributed
  coordination, and provider-side exactly-once or hard-stop guarantees.

## Delivered behavior

- Strict immutable version-1 metrics, units, provenance, policies, reservations, settlements,
  decisions, snapshots, stable IDs, canonical serialization, and repository port.
- `MEASURED` local context bytes and validation duration, `PROVIDER_REPORTED` structured token
  usage, `ESTIMATED` Decimal currency with estimator identity, and numeric-free `UNAVAILABLE`.
  `UNRESOLVED` is a separate reservation lifecycle state.
- SQLite migration 4 with append-only metadata-only usage/reservation/settlement storage,
  foreign keys, indexes, triggers, idempotent identical retries, and explicit conflicts.
- `BEGIN IMMEDIATE` atomic reservation over settled usage plus active reservations and atomic
  usage-plus-settlement. Integer and Decimal boundaries are exact; floats are not money.
- Builder, reviewer, local-builder repair, Codex repair, and validation persist intent before
  reservation and outcome before settlement. Role attempt usage derives from durable activity;
  the state machine remains the only authoritative `Run` attempt-counter owner.
- Validation timeout plans are conservatively capped without increasing any command timeout.
  Duration is measured from trusted evidence; overage is stored honestly and stops later work.
- Restart settles persisted agent/validation outcomes without replay. Missing trusted outcomes
  retain or become `UNRESOLVED`; reservations never expire merely with time.
- Hard token/cost budgets fail before invocation when no finite per-invocation ceiling or cost
  estimator exists. Missing provider usage and cost remain unavailable rather than zero.
- Real-file SQLite concurrency tests cover final attempt/token/duration/Decimal-cost capacity,
  identical/conflicting reservation and settlement races, rollback, stale revisions, reopen,
  and cross-role operation identities.
- Privacy and architecture tests reject raw prompt/context/source/output/secret fields and keep
  telemetry free of provider implementations, SQLite, subprocess, Git, network, transitions,
  and current-pricing lookup.

## Completion evidence

Canonical formatting, Ruff, strict mypy, grouped and top-level pytest, doctor, architecture/
security scans, and `git diff --check` passed on Windows after the final P5-002 audit. The exact
post-documentation baseline is recorded in `docs/PROJECT_STATE.md`. Verification used only
fake agents, controlled local commands, temporary SQLite, and existing temporary Git fixtures;
no live provider or network execution occurred.

## Known limitations

- Provider usage depends on validated structured reporting; missing metrics are unavailable.
- Ambiguous external execution can retain unresolved capacity for human recovery.
- No current pricing is bundled or fetched; estimated cost is not billing truth.
- Hard token/cost limits cannot guarantee provider-side stopping without a supported ceiling.
- Local byte measurements are not provider tokens.
- SQLite coordinates one local database and is not a distributed lock service.
- No live-provider certification has occurred.

- **Architectural decision:** ADR-0011.
- **Next package:** P6-001 — Configuration, Initialization, Doctor, and Provider-Detection UX.
