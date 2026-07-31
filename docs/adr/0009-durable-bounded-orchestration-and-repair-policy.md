# ADR-0009: Durable bounded orchestration and explicit repair authority

- **Status:** Accepted
- **Date:** 2026-07-31

## Context

The accepted run state machine, SQLite repository, owned Git worktrees, agent adapters,
validation runner, and local review gate existed as independent library boundaries.
Composing them requires a crash-explainable side-effect protocol without claiming
exactly-once external execution or allowing an agent to choose repair authority.

## Decision

Use a provider-neutral `OrchestrationService` as the sole workflow coordinator. It calls
the existing `transition_run` function for every state change and persists through the
`RunRepository`. External work begins only after an immutable version-1 attempt intent is
appended to an `OrchestrationJournal` under the current run revision and state. Stable IDs
derive from run, step, and sequence. A normalized outcome is appended after execution;
provider text and command stream text are projected out while bounded structured evidence
and artifact references remain.

SQLite schema migration 2 adds the append-only `orchestration_records` table with per-run
ordering, unique attempt/stage boundaries, normalized correlation columns, strict JSON,
foreign-key ownership, and update/delete triggers. Journal writes compare the complete
current run snapshot and revision in `BEGIN IMMEDIATE`. A duplicate stable boundary is an
idempotent non-creation result; a competing record or stale run fails before launch.

An outcome at the current durable state/revision is reused after restart, so a crash
between outcome persistence and transition cannot reinvoke the attempt. An intent without
an outcome is explicitly reconciled. Live owned-worktree evidence may establish a created
workspace only when the active record exactly matches the durable run, source/target paths,
branch, and common repository identity; another run's record is incompatible. Incomplete
mutating builder/repair/validation boundaries are ambiguous and are not automatically
replayed. This is durable at-most-once initiation with reconciliation, not exactly-once
external execution.

`RepairPolicy` is pure and deterministic. It returns `LOCAL_BUILDER`, `CODEX_REPAIR`,
`NO_REPAIR`, or `BLOCKED` plus typed reasons and bounded canonical defect fingerprints.
First mechanical failures may use a write-capable local builder. Repetition, malformed
builder repetition, or high-risk evidence requires a repair-capable Codex adapter and
explicit write authorization. Limits, cancellation, invalid evidence, scope violations,
and unresolved side effects forbid automatic repair. Decisions are durable in repair
intents or terminal transition metadata.

## Alternatives considered

An in-memory loop was rejected because crashes would erase intent and allow duplicate
writes. Storing attempts inside mutable run JSON was rejected because it would enlarge
state-transition payloads and weaken append-only evidence. Retrying ambiguous provider
calls was rejected because timeouts or malformed terminals can follow repository writes.
Letting provider verdicts choose repair or approval was rejected because authority and
local evidence must remain local. A distributed lock, queue, or exactly-once claim exceeds
the local MVP and cannot close the process-crash launch window.

## Consequences

All loops and collections have static bounds. Validation follows every potentially
mutating build or repair, and only a locally produced `ApprovalGate` can enter `APPROVED`.
An in-flight reviewer cancellation enters `CANCELLED` even though the lower-level review
gate conservatively classifies a non-completed reviewer response as blocked. Worktree
verification precedes every risky phase; the coordinator never cleans, commits, merges,
pushes, or publishes. Terminal results are idempotent and persisted limit outcomes survive
replay. A crash after durable intent but before the external program starts is
indistinguishable from a crash during that program; mutating work is preserved and blocked
for later human/Phase-6 recovery. P4-002 provides a library reconciliation entry point but
no user-facing run/resume/status/report command.
