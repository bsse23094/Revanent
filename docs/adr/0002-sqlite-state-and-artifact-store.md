# ADR-0002: SQLite state plus versioned run artifacts

## Context

Runs must resume safely, preserve audit history, reference large evidence, and work
without a server.

## Decision

Use SQLite for normalized current state and append-only significant events, with
versioned JSON/JSONL/Markdown artifacts under `.revanent/runs/<run-id>/`. State and
its event update transactionally. Large command/provider output remains in bounded
artifact files referenced from records.

## Alternatives considered

JSON files alone simplify inspection but make concurrency, atomic multi-record updates,
and querying fragile. Event sourcing alone increases replay/migration complexity.
PostgreSQL or a message broker violates the local MVP boundary.

## Consequences

Schema migrations and artifact compatibility rules are required. Backups must treat
database and artifacts consistently. The storage port keeps SQLite out of domain logic.

## P1-002 implementation

Schema version 1 uses `schema_migrations`, revisioned `runs`, and append-only
`run_events`. Run and event payloads retain the complete versioned Pydantic JSON while
normalized columns enforce identifiers, versions, states, timestamps, ordering, and
foreign-key integrity. Per-run event sequence is canonical; update/delete triggers
make events immutable. Initial run creation is revision zero and emits no event.

Each accepted transition uses `BEGIN IMMEDIATE`, compares the full expected run and
revision, updates the run, and inserts exactly one event in one transaction. A stable
event ID is the idempotency key. Replaying the immediately committed identical request
returns the existing result; competing stale revisions fail. Connections are
short-lived, enable and verify foreign keys, and read-only operations cannot create an
absent database.

Migration definitions are an ordered inspectable tuple. Initialization applies
pending forward migrations transactionally, validates exact recorded names/versions,
and rejects newer, incomplete, or malformed histories without repair. Future versions
append one migration and raise the supported schema constant; migrations are
forward-only. Artifact files are not created by P1-002 because no artifact-reference
model exists in the authoritative P1-001 domain yet.

## P4-002 extension

Forward migration 2 adds an append-only orchestration journal. Each record binds a stable
record/attempt ID, run and work-package correlation, per-run sequence, expected run
revision/state, step, stage, UTC time, and strict version-1 payload. Unique stage and
sequence constraints, run foreign-key ownership, JSON/normalized-column checks, and
update/delete triggers make durable intent, outcome, and reconciliation evidence explicit.
Journal insertion uses the same short-lived connection, foreign-key enforcement,
`BEGIN IMMEDIATE`, and complete optimistic run-snapshot comparison as transition writes.
Version-1 databases migrate forward without changing existing runs or events.

## P5-001 extension

Forward migration 3 transactionally rebuilds the orchestration table's attempt-kind check to
admit `CONTEXT`, copies existing P4 rows in canonical run/sequence order, drops the prior table,
and recreates its index and append-only triggers. It adds no table and does not persist selected
context bodies. Context intents and metadata-only version-1 manifests use the existing strict
record payload, normalized correlation, revision/state guard, and idempotency rules. Schema-2
databases migrate forward without changing runs, events, or P4 orchestration evidence.

## P5-002 extension

Forward migration 4 adds append-only `usage_records`, `budget_reservations`, and
`budget_settlements` with run foreign keys, normalized metric/provenance/idempotency columns,
strict versioned JSON, indexes, and update/delete triggers. `BEGIN IMMEDIATE` atomically
evaluates settled usage plus active reservations before inserting capacity. Usage and its
settlement commit together; identical retries are idempotent and conflicts fail. Integer and
Python `Decimal` arithmetic remain exact. Metadata contains no prompts, context bodies, source,
raw output, credentials, or environment values. Schema-3 databases migrate without changing
existing run, event, orchestration, or context evidence.

## P6-002-C1 extension

Forward migration 5 adds the one-to-one `runtime_bindings` table with a Run foreign key, full
strict schema-v1 repository identity JSON, deterministic unique worktree ID, normalized
repository-relative target, branch, and UTC creation evidence. Update/delete triggers make the
binding immutable. `create_bound_run` inserts the revision-zero Run and binding in one transaction;
any binding/correlation failure rolls back both before orchestration can launch. Existing schema-4
library Runs remain readable through `create_run`, while runtime application operations require a
binding and report its absence as corrupt evidence rather than inventing identity.

## Status

Extended through SQLite schema version 5 by P6-002-C1 on 2026-08-01.

Accepted — 2026-07-29; SQLite portion implemented and verified in P1-002 on 2026-07-30.
