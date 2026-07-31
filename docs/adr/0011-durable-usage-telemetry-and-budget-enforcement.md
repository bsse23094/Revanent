# ADR-0011: Durable usage telemetry and budget enforcement

## Context

Revanent must explain local context size, validation time, provider-reported token use,
attempt consumption, and optional cost estimates without confusing unlike units or trusting
provider prose. A preflight check followed by a separate insert permits concurrent local
coordinators to overspend. Postflight-only accounting launches work after a hard limit has
already been crossed. Crashes between invocation, outcome persistence, and accounting also
make automatic replay unsafe.

## Decision

Telemetry is provider-neutral metadata owned by strict version-1 port models and a pure
`TelemetryService`; SQLite is one adapter behind `TelemetryRepository`. Metrics carry one
explicit unit. Local context is measured in bytes, validation in milliseconds, attempts in
counts, provider usage in tokens, and cost in decimal currency. Bytes are never converted to
tokens.

Provenance is part of each immutable record:

- `MEASURED` is trusted local measurement, including context bytes and validation duration.
- `PROVIDER_REPORTED` is structured provider usage and is never relabelled measured.
- `ESTIMATED` requires a Decimal value, currency, and stable estimator identity. It is not
  actual, billed, or charged cost.
- `UNAVAILABLE` has no numeric value and carries a stable reason code; unknown is not zero.
- `UNRESOLVED` is reservation lifecycle evidence, not usage provenance. It means launch,
  completion, or settlement cannot be safely established and capacity remains unavailable.

SQLite migration 4 adds append-only usage, reservation, and settlement tables with foreign
keys, stable canonical IDs, uniqueness constraints, indexes, and update/delete prevention.
`reserve_if_allowed` uses `BEGIN IMMEDIATE` to compare settled usage plus active reservations
and insert one reservation atomically. `settle_reservation` atomically appends normalized
usage and the matching settlement. Identical retries are idempotent; payload conflicts fail.
Integer metrics use integer arithmetic. Currency uses Python `Decimal`; binary floats are not
accepted for monetary evidence.

The durable invocation order is intent, reservation, adapter invocation, normalized outcome,
then settlement. Validation follows the same order with a derived plan whose aggregate
whole-second timeouts never exceed the declared plan or remaining duration. Measured overage
is stored without clamping and blocks later consumption. The authoritative `Run` attempt
counters remain state-transition evidence; telemetry derives role-specific attempt usage and
does not create a second counter.

At restart, a persisted normalized agent outcome or validation result regenerates the same
settlement without reinvocation. An active reservation without trusted outcome evidence is
marked `UNRESOLVED`; it is never deleted or expired by time. A stale coordinator performs no
reconciliation write. SQLite serializes writers for one local database but is not a
distributed coordination system.

Only bounded IDs, metrics, units, values, provenance, provider/model identifiers,
correlation, timestamps, reason codes, and lifecycle evidence are stored. Prompts, context
bodies, source, raw provider/command output, credentials, environment values, and host
metadata are outside telemetry contracts. Provider prose cannot supply usage or limits.

No current pricing table is bundled or fetched. When no estimator exists, cost is
`UNAVAILABLE`. A configured hard token or cost budget without a finite per-invocation ceiling
blocks before invocation and consumes no attempt. Revanent cannot guarantee provider-side
hard stopping beyond a ceiling actually supplied to a provider.

## Alternatives considered

- Read-check-then-insert was rejected because concurrent writers can both pass.
- Postflight-only enforcement was rejected because it launches already-denied work.
- Unknown-as-zero was rejected because it silently restores capacity.
- Byte-to-token heuristics were rejected because tokenization is provider/model dependent.
- Automatic reservation expiry was rejected because time does not prove non-execution.
- Duplicate telemetry-owned attempt counters were rejected in favor of durable orchestration
  intents and authoritative `Run` transition counters.
- Replaying external work after settlement failure was rejected because the durable outcome,
  not accounting failure, owns whether execution occurred.
- Current pricing lookup was rejected because it adds network, volatility, and billing claims.

## Consequences

Hard local limits and concurrent writes are deterministic within one SQLite database. Missing
provider reporting and absent pricing remain visible rather than fabricated. Ambiguous work
may require human recovery while reserved capacity remains unavailable. Metadata is durable,
but provider-side execution is not exactly once and live-provider certification remains a
later package.

## Status

Accepted — 2026-07-31. Implemented and locally/fake verified by P5-002.
