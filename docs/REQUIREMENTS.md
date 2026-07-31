# Requirements

Status terms are **MUST**, **SHOULD**, and **MAY** in their ordinary normative sense.

## Functional

- **FR-001** Accept a bounded task with allowed/forbidden scope and stable identity.
- **FR-002** Inspect a target Git repository without overwriting unexplained changes.
- **FR-003** Build a deterministic, reason-annotated, task-specific context package.
- **FR-004** Create/select an isolated, run-specific Git worktree when possible.
- **FR-005** Invoke builders and reviewers through provider-neutral typed contracts.
- **FR-006** Support fake, OpenCode builder, and Codex reviewer/repair adapters.
- **FR-007** Run a declared validation plan after every code change.
- **FR-008** Parse a versioned review result with APPROVED, CHANGES_REQUIRED, or BLOCKED.
- **FR-009** Select and record a bounded local-builder or Codex repair strategy.
- **FR-010** Enforce attempt, duration, token/cost, scope, and cancellation limits.
- **FR-011** Persist run state and significant append-only events for safe resume.
- **FR-012** Generate JSON and Markdown evidence reports and artifact references.
- **FR-013** Provide init, doctor, run, resume, status, and report CLI workflows.
- **FR-014** Detect installed provider versions/capabilities without assuming flags.
- **FR-015** Reject approval when validation, parsing, scope, or evidence gates fail.

## Non-functional

- **NFR-001** Support Python 3.12+ on current Windows and maintained Linux runners.
- **NFR-002** Keep domain/orchestration independent of provider and storage adapters.
- **NFR-003** Be deterministic under fake providers and fixture repositories.
- **NFR-004** Fail explicitly on invalid states, schemas, or configuration.
- **NFR-005** Remain locally operable without a hosted Revanent service.
- **NFR-006** Use typed interfaces and strict static analysis for project code.
- **NFR-007** Bound retained command output and reference full artifacts when retained.
- **NFR-008** Distinguish measured, reported, and estimated usage/cost values.

## Security

- **SEC-001** Execute external commands as argument lists without shell interpolation by default.
- **SEC-002** Restrict working directories, executable policy, timeout, output, and cancellation.
- **SEC-003** Filter inherited environment variables and redact secret-like values.
- **SEC-004** Enforce normalized allowed and forbidden path boundaries.
- **SEC-005** Never force, destructively reset, delete untracked work, push, or merge by default.
- **SEC-006** Treat repositories, provider output, command output, and model content as untrusted.
- **SEC-007** Do not persist credentials or excessive duplicate source content.
- **SEC-008** Require tests for security-sensitive command, Git, persistence, and recovery changes.

## Operational

- **OPS-001** Persist enough state to explain and reproduce each completed or blocked run.
- **OPS-002** Make resume idempotent and verify repository/worktree assumptions first.
- **OPS-003** Preserve failed worktrees when configured and clean only owned worktrees.
- **OPS-004** Mark checks as passed, failed, skipped, simulated, or unavailable accurately.
- **OPS-005** Emit concise progress and durable state-transition history.
- **OPS-006** Validate configuration before invoking an agent.
- **OPS-007** Keep schema and artifact format versions explicit and migratable.
- **OPS-008** Provide opt-in, clearly marked live-provider tests.

Each work package names the requirements it advances; complete traceability is
maintained in `ROADMAP.md` and package completion evidence.

## Implemented traceability

| Requirement | Package | Evidence |
|---|---|---|
| FR-001 | P1-001 | Stable task/work-package/run IDs and bounded `TaskSpecification` scope |
| FR-008 | P1-001 | Versioned `ReviewResult` with the three canonical verdicts |
| FR-010 | P1-001 | Typed duration/attempt/token/cost limits and bounded attempt counters |
| FR-015 | P1-001 | Local `ApprovalGate` required for `REVIEWING` to `APPROVED` |
| NFR-002 | P1-001 | `revanent.domain` imports no adapters, CLI, or providers |
| NFR-004 | P1-001 | Strict schema errors and typed invalid-transition/approval errors |
| OPS-006 | P1-001 | Safe YAML loader validates the full configuration before use |
| OPS-007 | P1-001 | Explicit schema version 1 and deterministic JSON round-trip contracts |
| FR-011 | P1-002 | Revisioned runs and append-only transition events commit atomically |
| NFR-003 | P1-002 | Canonical sequences and validated payload reload are deterministic |
| OPS-001 | P1-002 | Current run plus ordered transition history reload after process close |
| OPS-002 | P1-002 | Revision guards and event idempotency prevent duplicated transitions |
| OPS-007 | P1-002 | SQLite schema version 1 and ordered forward-only migration runner |
| NFR-007 | P2-001 | Independently bounded stream capture plus typed redacted overflow references |
| SEC-001 | P2-001 | Sole production adapter launches executable/argument lists with `shell=False` |
| SEC-002 | P2-001 | Central executable/cwd/resource policy, timeout, cancellation, and cleanup |
| SEC-003 | P2-001 | Bounded selected child environment and central result/artifact redaction |
| SEC-004 | P2-001 | Resolved structural containment with traversal/link/junction/case/UNC tests |
| SEC-008 | P2-001 | Adversarial command contract, policy, architecture, and integration tests |
| FR-002 | P2-002 | Porcelain-v2/NUL inspection refuses malformed, dirty, conflicted, or active-operation state |
| FR-004 | P2-002 | Dedicated-branch linked worktrees are created from an exact base and live-verified |
| NFR-001 | P2-002 | Real Windows Git/junction tests plus the existing Windows/Linux Python CI matrix |
| NFR-003 | P2-002 | Deterministic machine-readable snapshots and temporary-repository lifecycle tests |
| NFR-004 | P2-002 | Typed Git/output/path/identity/ownership/cleanup failures preserve evidence |
| OPS-002 | P2-002 | Live identity/registry/path/branch/base checks precede verification and cleanup |
| OPS-003 | P2-002 | Versioned owned records preserve partial work and authorize only safe normal removal |
| OPS-007 | P2-002 | Git and ownership schema version 1 reject unknown versions/fields |
| SEC-005 | P2-002 | No force/reset/clean/prune/push/merge/branch deletion; dirty or unowned cleanup refuses |
| SEC-008 | P2-002 | Adversarial Git contract/parser/ownership/real-repository tests on security boundaries |
| FR-005 | P3-001 | Strict typed builder/reviewer/repairer request-response envelopes and `AgentAdapter` port |
| FR-006 | P3-001 (partial) | Deterministic fake adapter only; OpenCode/Codex remain P3-002 |
| NFR-002 | P3-001 | Agent port imports no provider, process, Git, storage, CLI, or orchestration implementation |
| NFR-003 | P3-001 | Exact request signatures, explicit timing, finite scripts, isolated replay, canonical JSON |
| NFR-004 | P3-001 | Typed capability/request/output failures and strict malformed-output rejection |
| SEC-006 | P3-001 (agent boundary) | Bounded strict UTF-8/JSON parsing, correlation, semantics, and fail-closed normalization |
| SEC-007 | P3-001 (agent boundary) | Environment names only, sensitive-value redaction, bounded artifact references, no raw persistence |
| OPS-004 | P3-001 (simulated) | Completed/failed/blocked/timeout/cancelled/invalid/unavailable outcomes remain distinct |
| OPS-007 | P3-001 | Explicit version 1 agent envelopes, capabilities, and artifact references |
| FR-005 | P3-002 | OpenCode/Codex implement the unchanged typed `AgentAdapter` request/response contract |
| FR-006 | P3-002 | OpenCode BUILDER, Codex read-only REVIEWER, and explicitly authorized REPAIRER adapters |
| FR-014 | P3-002 | Version/top-level/subcommand help detection freezes supported CLI surfaces and fails closed |
| NFR-002 | P3-002 | Provider modules depend on ports, not command/Git/storage/orchestration implementations |
| NFR-003 | P3-002 | Finite fake executables prove deterministic provider execution without network/credentials |
| NFR-004 | P3-002 | Unavailable/incompatible/command/JSONL/envelope outcomes remain typed and distinct |
| SEC-001 | P3-002 | Fixed argument tuples and bounded stdin execute exclusively through `CommandRunner` |
| SEC-002 | P3-002 | Existing executable/cwd/environment/timeout/cancellation/output policy remains authoritative |
| SEC-003 | P3-002 | Typed allowlisted environment, sensitive-material rejection, and layered redaction |
| SEC-006 | P3-002 | Strict known JSONL events precede P3-001 parsing; provider paths/claims stay untrusted |
| SEC-007 | P3-002 | No secret config fields, raw output persistence with secrets, or repository-content prompts |
| OPS-004 | P3-002 | Doctor labels OpenCode unavailable and Codex compatible from safe local inspection |
| OPS-008 | P3-002 | Default tests are fake-only; no live test or model invocation exists |
| FR-007 | P4-001 | Ordered declared required/advisory commands execute only through `CommandRunner`; aggregate success is local |
| FR-008 | P4-001 | Correlated P3 REVIEWER payload reuses canonical version-1 `ReviewResult` and deterministic local finding IDs |
| FR-015 | P4-001 | Pure local gate replays validation and creates `ApprovalGate` only after every local invariant passes |
| NFR-002 | P4-001 | Validation/gate modules import no concrete command, provider, Git, storage, CLI, or orchestration implementation |
| NFR-003 | P4-001 | Injected evidence/timestamps, explicit order, canonical JSON, fake commands, and fake reviewer yield deterministic replay |
| NFR-004 | P4-001 | Malformed, incomplete, mismatched, interrupted, blocked, or ambiguous evidence returns typed refusal |
| NFR-006 | P4-001 | Strict typed plan/result/local-evidence/decision boundaries pass mypy |
| NFR-007 | P4-001 | Bounded separate streams and contained, redacted, correlated relative artifact references |
| SEC-001/002/003/004 | P4-001 | Literal requests reuse controlled executable/cwd/environment/timeout/output/cancellation policies |
| SEC-006/007 | P4-001 | Provider prose/claims remain untrusted; decisions omit raw payload and credential values |
| SEC-008 | P4-001 | Approval-bypass, artifact, cancellation, chronology, identity, and architecture negative tests |
| OPS-004 | P4-001 | Passed/advisory/failed/timed-out/cancelled/blocked/unavailable/invalid/not-run outcomes stay distinct |
| OPS-007 | P4-001 | Validation plans/results/artifacts and gate evidence/decisions use strict schema version 1 |
| FR-009 | P4-002 | Pure policy durably selects bounded `LOCAL_BUILDER`, explicitly authorized `CODEX_REPAIR`, `NO_REPAIR`, or `BLOCKED` with deterministic reasons/fingerprints |
| FR-010 | P4-002 | Builder/review/repair counters, validation-plan supply, total duration, cancellation, and a static coordinator bound are checked before side effects |
| FR-011 | P4-002 (library) | SQLite migration 2 persists ordered intent/outcome/reconciliation evidence and stable transition events; user-facing resume remains P6-002 |
| FR-015 | P4-002 | Every mutating attempt forces validation; only the local P4-001 `ReviewGate` result supplies approval evidence to `transition_run` |
| NFR-002 | P4-002 | Coordinator depends on provider-neutral ports and immutable evidence, never concrete provider/Git/SQLite/CLI/process implementations |
| NFR-003 | P4-002 | Injected clocks/IDs, finite fake agents/commands, canonical ordering, and stable boundaries make fake orchestration deterministic |
| NFR-004 | P4-002 | Stale state, invalid evidence, missing tooling, ownership mismatch, ambiguous writes, exhausted limits, and terminal outcomes fail explicitly |
| NFR-006 | P4-002 | Strict version-1 orchestration requests/results/attempts/decisions/reconciliation pass mypy and reject unknown fields/versions |
| SEC-005 | P4-002 | Coordinator exposes no cleanup, force, reset, clean, commit, push, merge, publication, or branch-deletion path |
| SEC-006 | P4-002 | Provider output is projected and remains untrusted; local validation, scope, ownership, and review correlation own authority |
| SEC-008 | P4-002 | Architecture, crash-window, stale-boundary, ambiguous-write, cancellation, limit, scope, and approval-bypass tests fail closed |
| OPS-001 | P4-002 | Terminal runs retain state events plus bounded attempt, gate, repair, limit, and reconciliation evidence sufficient to explain outcomes |
| OPS-002 | P4-002 | Durable outcomes resume without reinvocation; missing mutating outcomes reconcile or block; exactly-once execution is not claimed |
| OPS-003 | P4-002 | Active run-bound owned worktree identity is live-verified before build, validation, review, and repair; no cleanup is attempted |
| OPS-004 | P4-002 | In-progress/approved/failed/blocked/cancelled/stale and attempt/limit/reconciliation outcomes remain explicit |
| OPS-005 | P4-002 | Every accepted state change and orchestration decision is persisted under optimistic run revision/state ownership |
| OPS-007 | P4-002 | Orchestration evidence schema version 1 and SQLite schema migration 2 are frozen and contract tested |
| FR-003 | P5-001 | Typed multi-source discovery, deterministic scope/priority/expansion, safe content handling, and reason-annotated canonical manifest |
| FR-005 | P5-001 | Bounded packages project through the existing provider-neutral `AgentRequest.context` field without provider syntax |
| FR-011 | P5-001 | CONTEXT_PREPARING persists append-only intent and metadata-only manifest outcome through SQLite migration 3 |
| NFR-002/003/004/006 | P5-001 | Context port contracts, injected selector/reader, canonical ordering/IDs, strict failures, and mypy-tested boundaries |
| NFR-007 | P5-001 | Per-source/item/artifact/aggregate limits, explicit UTF-8 truncation, exclusion, deduplication, and byte accounting |
| SEC-003/004/006/007 | P5-001 | Redaction/credential refusal, structural scope/link/race checks, provenance labels, and no raw body persistence |
| SEC-008 | P5-001 | Adversarial discovery/path/junction/race/secret/injection/artifact/manifest/orchestration tests |
| OPS-001/002/007 | P5-001 | Durable manifest evidence, exact continuation match, strict context/schema/storage versions, and forward migration 3 |
| FR-010 | P5-002 | Provenance-labelled context bytes, validation duration, role attempts, structured provider tokens, optional Decimal cost evidence, and exact hard-budget decisions |
| NFR-002/003/004/006 | P5-002 | Provider-neutral immutable contracts, deterministic canonical IDs, fail-closed unavailable/unresolved behavior, and strict typed boundaries |
| NFR-008 | P5-002 | Atomic reservation before launch, atomic settlement after outcome, exact integer/Decimal boundaries, overage denial, and real SQLite race coverage |
| SEC-003/006/007/008 | P5-002 | Metadata-only persistence, no provenance laundering/bytes-to-token inference/provider-controlled limits, and adversarial privacy/concurrency tests |
| OPS-001/002/005/007 | P5-002 | Durable usage lifecycle, restart settlement without replay, unresolved preservation, and SQLite migration 4 |

P4-002 now owns library-level repository/worktree verification and in-flight side-effect
reconciliation. P6-002 remains responsible for the user-facing resume/status/report
workflow and must call these boundaries rather than reimplementing them.
