# ADR-0006: Versioned agent envelope and deterministic fake adapter

## Context

OpenCode, Codex, later orchestration, and validation need one boundary that does not
leak CLI result dictionaries or allow provider prose to acquire workflow authority.
Provider output is untrusted and can be malformed, oversized, secret-bearing, falsely
correlated, or ambiguous about side effects. Tests also need repeatable provider
behavior without network access, installed tools, credentials, or paid calls.

## Decision

`revanent.ports.agents` owns immutable strict version-1 capability, request, response,
status, failure, usage, diagnostic, role-payload, and artifact-reference contracts plus
the `AgentAdapter` protocol. Every response correlates the invocation, run, work
package, attempt ID/number, role, and expected response schema. Builder, reviewer, and
repairer roles remain distinct; only builder/repairer requests may require repository
writes, and reviewer requests are read-only. Capabilities and requested authority are
explicit facts rather than model-name inference.

Untrusted envelopes are byte-bounded before strict UTF-8 and JSON parsing. Parsing
rejects duplicate keys, trailing content, non-standard numbers, excessive depth/items,
unknown versions/fields/enums, and invalid typed values. Parsing, correlation, and
request-specific semantics are separate checks. Any rejection becomes a sanitized,
request-correlated `INVALID_OUTPUT`; malformed bytes are never included in errors.
Known sensitive values are replaced before model validation. Raw output can be exposed
only through a redacted, bounded, relative reference below a separately authorized
artifact-root identity; P3-001 does not implement the durable artifact store.

`FakeAgentAdapter` implements the same port using an immutable finite scenario. Each
step carries an exact canonical request SHA-256, explicit UTC start and duration,
bounded cancellation checkpoints, and either a typed outcome or bounded raw bytes sent
through the strict parser. Compatibility, pre-cancellation, request mismatch, and
unavailability do not consume a step. Once scripted execution starts, timeout or
mid-invocation cancellation consumes the step and records possible side effects. A
new adapter over the same scenario is replay from the initial state; there is no
durable response cache. Per-instance locking serializes access and state never leaks
between instances.

## Alternatives considered

Provider-specific dictionaries were rejected because they would move validation and
routing conditionals into orchestration. Reusing command `ArtifactReference` was
rejected because it names an absolute command-stream file rather than an agent artifact
relative to a separately approved run root. Callbacks in fake scripts were rejected
because arbitrary executable test logic would defeat declarative determinism. Wall
clocks, generated UUIDs, sleeps, unbounded response queues, YAML, pickle, and permissive
JSON parsing were rejected for determinism and trust-boundary reasons.

## Consequences

Future live adapters must detect capabilities, validate requests before execution,
invoke tools only through the controlled-command port, sanitize retained output, and
return these exact contracts. Provider claims about files, commands, findings, or
approval remain evidence claims; they cannot mutate `Run`, create `ApprovalGate`, or
bypass Git and validation checks. Version or envelope changes require an explicit new
schema/ADR decision. The fake is simulated evidence only and cannot satisfy the Phase 3
live-adapter exit gate.

## Status

Accepted and implemented - 2026-07-30.
