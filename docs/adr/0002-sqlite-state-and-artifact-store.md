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

## Status

Accepted — 2026-07-29; implementation scheduled for P1-002.
