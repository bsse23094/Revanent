# ADR-0003: Strict versioned domain and configuration schemas

## Context

Run state, task scope, budgets, review evidence, and project configuration cross
multiple future adapter boundaries. Permissive dictionaries, mutable run records, or
provider-owned approval decisions would make invalid states easy to create and older
records ambiguous to reload.

## Decision

Use immutable Pydantic version-1 models with forbidden extra fields for configuration
and durable domain boundaries. Stable IDs use validated value types. Wire enums use
explicit uppercase values. A `Run` may change state only through the central
`transition_run` function, which returns a validated next run and matching immutable
`StateTransition`; Pydantic's unvalidated `model_copy(update=...)` path is disabled
for runs.

Approval requires structured reviewer output plus locally computed validation,
parsing, scope, generated-file, evidence, and dirty-state gates. Configuration YAML
is loaded with `safe_load`, prechecks `schema_version`, rejects unknown fields and
unsafe bounds/path forms, and cannot enable push or merge in schema version 1.

## Alternatives considered

Dataclasses plus handwritten serializers would reduce the runtime dependency but
duplicate validation and JSON-schema behavior. Mutable models with validation on
assignment would still expose transition-rule bypasses. Provider prose as approval
would violate the local evidence gate. Supporting multiple schema versions before a
second version exists would add speculative migration code.

## Consequences

Callers receive explicit validation or domain errors and must use the state machine.
Serialized records carry version fields and round-trip deterministically. Adding a
schema version requires an explicit migration or rejection rule. P1-002 now persists
the returned run and a versioned `RunEvent` envelope atomically without reimplementing
transition rules.

## Status

Accepted — 2026-07-30.
