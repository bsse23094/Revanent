# ADR-0008: Deterministic Validation Evidence and Local Review Gates

- **Status:** Accepted
- **Date:** 2026-07-30

## Context

Provider claims that commands passed or that a change is safe are untrusted. Approval
needs replayable command evidence, a strictly correlated structured review, and facts
that only local Revanent code can establish. The accepted version-1 `ReviewResult` and
`ApprovalGate` contracts must remain authoritative rather than being replaced by a
second provider-controlled approval schema.

## Decision

`revanent.ports.validation` owns strict immutable version-1 validation plans, ordered
command specifications, command results, aggregates, summaries, failures, and relative
artifact references. A plan contains at least one required command. Commands name a
simple executable capability and literal argument tuple separately. Execution uses only
the injected `CommandRunner`; it never uses a shell, retries, Git, persistence, an agent,
or a run transition.

Required commands must all pass. Advisory failures are visible and are acceptable only
when the plan explicitly enables them, every required command passes, and all evidence
is complete. Cancellation always ends further plan execution. Explicit fail-fast emits
`NOT_RUN` evidence for later commands. Aggregate results are replayed against the exact
plan, order, identities, correlations, classifications, expected exits, and chronology.

`revanent.review.ReviewGate` is a pure local service. It accepts the original plan and
aggregate, a completed and strictly typed REVIEWER `AgentResponse`, and the smallest
separate `LocalApprovalEvidence` record for scope, generated files, lockfiles, required
artifacts, cleanliness, read-only authority, side-effect reconciliation, adapter
identity, and observation chronology. It derives stable finding IDs by sorting the
accepted version-1 `ReviewFinding` values by severity and summary. This preserves the
existing domain review contract, which intentionally has no provider-supplied
`safe_to_approve`, validation-assessment, file, category, line, or approval-gate fields.
Unknown fields remain rejected.

Only a reason-free `APPROVABLE` decision constructs the existing domain
`ApprovalGate`. Every false, absent, malformed, mismatched, incomplete, interrupted, or
ambiguous condition returns a typed refusal with no approval evidence. The service does
not transition or persist a `Run`, choose repair, invoke an agent, execute validation,
or touch Git.

## Consequences

- Provider prose and claimed command outcomes never become validation or approval.
- Versioned canonical JSON permits deterministic result and decision replay.
- Complete redacted overflow artifacts can satisfy an explicitly complete-output rule;
  path, stream, filename, size, and correlation are checked under the approved root.
- The local evidence producer and orchestration/state-transition wiring remain P4-002.
- Artifact existence checks remain subject to same-user filesystem replacement races;
  version 1 has no content digest for command overflow artifacts.
