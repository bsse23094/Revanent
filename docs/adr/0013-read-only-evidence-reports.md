# ADR-0013: Read-only canonical evidence reports

- **Status:** Accepted
- **Date:** 2026-08-01

## Context

Operators need a portable explanation of a Run without allowing a presentation command to repair,
reconcile, invoke a provider, or manufacture approval facts. Durable evidence can be incomplete or
contradictory, and report files must not overwrite unrelated user files.

## Decision

P6-002 owns a strict immutable schema-v1 `EvidenceReport` assembled read-only from the same status
projection and canonical durable Run, binding, event, attempt, context, validation, review,
ApprovalGate, telemetry, reservation, and artifact metadata evidence. Contradictions produce
`INVALID_EVIDENCE`; active or incomplete evidence produces `INCOMPLETE`; an APPROVED Run requires
independent gate, validation, review, reservation, and ambiguity checks.

Canonical UTF-8 JSON is the authoritative report projection. Markdown is a deterministic, escaped,
bounded pure rendering of that object. Both omit task/context/source/provider/command bodies,
secrets, raw environment, raw exceptions, and unnecessary absolute paths. Integrity metadata is a
SHA-256 digest and explicitly is not a signature or authenticity proof.

Artifacts are written only for an explicit report-root-relative path. The local writer refuses
absolute/traversing, `.git`, link/reparse, special, and collision targets; it writes a private
temporary file, flushes/fsyncs, and uses create-exclusive linking. Identical existing bytes are
reused; differing bytes are refused. No report body or report index is persisted in SQLite.

## Alternatives rejected

- Rendering Markdown directly from storage: would create a second evidence collector.
- Persisting complete report bodies in SQLite: duplicates derivable evidence and increases privacy.
- Model-generated summaries: cannot be deterministic evidence.
- Force/overwrite output: risks user data loss.
- Reconciling during report generation: violates read-only inspection semantics.
- Calling report digests signatures: a digest alone provides no authenticity guarantee.

## Consequences

Reports are deterministic for a fixed injected generation timestamp and evidence revision. They are
useful local audit artifacts, not a snapshot of external provider state or a live-provider
certification. No migration is required.
