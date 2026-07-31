# Project State

Last updated: 2026-07-31

## Current milestone

Phase 4 - Orchestration and Repair is complete. P4-001 validation/review gates and
P4-002 bounded durable orchestration, reconciliation, and explicit repair policy are
implemented and verified. Phase 5 is next; P5-001 context selection is not started.

## Completed work packages

- P0-001 - Repository Foundation (COMPLETE 2026-07-29).
- P1-001 - Domain, Configuration, and State Machine (COMPLETE 2026-07-30).
- P1-002 - Durable Run State and Events (COMPLETE 2026-07-30).
- P2-001 - Controlled Commands and Path Policy (COMPLETE 2026-07-30).
- P2-002 - Safe Git Worktrees (COMPLETE 2026-07-30).
- P3-001 - Agent Contracts and Deterministic Fake Adapter (COMPLETE 2026-07-30).
- P3-002 - OpenCode and Codex Capability Detection and Adapters (COMPLETE 2026-07-30).
- P4-001 - Validation and Structured Review Gates (COMPLETE 2026-07-30).
- P4-002 - Bounded Orchestration and Explicit Repair Policy (COMPLETE 2026-07-31).

## Active work package

None. P5-001 is the next planned package.

## P4-002 delivered

- Strict immutable version-1 orchestration requests, results, attempt IDs and records,
  workspace/build/validation/review/repair evidence, statuses, limits, repair decisions,
  side-effect certainty, reconciliation results, canonical JSON, clocks/ID factories,
  local-evidence collection, and journal protocols.
- A provider-neutral finite coordinator over `RunRepository`, `GitRepository`,
  `AgentAdapter`, `ValidationExecutor`, `ReviewGate`, cancellation, and injected clock/IDs.
  Every transition uses `transition_run`; no concrete provider, Git, command, SQLite, CLI,
  process, or network implementation is imported.
- SQLite forward migration 2 with an append-only orchestration journal. Stable intent,
  outcome, and reconciliation records are revision/state guarded, sequenced, correlated,
  bounded, strict, foreign-key owned, and update/delete protected. Version-1 databases
  migrate without changing existing runs.
- Durable at-most-once initiation: intent is persisted before launch; completed outcomes
  at the current state/revision are reused after restart; stable duplicate boundaries are
  idempotent; stale writers fail before launch. Incomplete mutating intent is inspected and
  never automatically replayed. This does not claim exactly-once external execution.
- Mandatory post-build/post-repair validation and read-only structured review. Provider
  prose cannot approve; only the P4-001 `ReviewGate` returns the `ApprovalGate` supplied to
  `REVIEWING -> APPROVED`. Scope, artifacts, generated/lock files, evidence, ownership,
  correlation, and unresolved side effects fail closed.
- Pure deterministic repair selection with `LOCAL_BUILDER`, `CODEX_REPAIR`, `NO_REPAIR`,
  and `BLOCKED`; bounded fingerprints escalate repeated/high-risk defects. Codex repair
  requires a capable REPAIRER plus explicit write authorization. Repair decisions and
  terminal limit outcomes are durable.
- Exact builder/reviewer/repair/duration bounds, durable cancellation (including
  in-flight reviewer cancellation precedence), terminal-state idempotency, exact
  intent-to-live-worktree reconciliation, owned-worktree verification before every risky
  phase, and conservative FAILED/BLOCKED mapping under the authoritative transition table.
- Fake-first SQLite E2E coverage proves direct approval, both repair paths, repetition
  escalation, limit exhaustion, missing tooling, scope/ownership refusal, cancellation,
  crash-after-outcome reuse, worktree-intent reconciliation, incomplete mutating intent
  refusal, and rollback-before-launch. No live/model/network call occurs.

## P4-001 delivered

- Immutable strict version-1 validation plans, required/advisory ordered commands,
  execution/output/artifact policy, command results, summaries, aggregates, failures,
  stable IDs, canonical serialization, and a provider-neutral executor protocol.
- Ordered execution exclusively through injected `CommandRunner`, with exact literal
  arguments/cwd/environment/output/timeout policy, no retries, typed status mapping,
  fail-fast `NOT_RUN`, terminal cancellation, safe artifact translation, and replay that
  rejects missing, duplicate, extra, out-of-order, mismatched, or invalid evidence.
- Pure strict version-1 `ReviewGate` inputs/decisions and `LocalApprovalEvidence`. The
  original plan/result are replayed; P3 response role/status/schema/payload/identity,
  run/work-package/invocation chronology, artifacts, canonical review verdict/findings,
  and every local scope/generated/lock/evidence/cleanliness/authority/side-effect fact
  fail closed with deterministic reason codes.
- Existing `ReviewResult`, `ReviewFinding`, and `ApprovalGate` remain authoritative.
  Stable finding IDs are derived locally. Only a reason-free `APPROVABLE` result creates
  a satisfied `ApprovalGate`; provider prose, claimed commands, or embedded approval do
  not. No run transition, orchestration, repair selection, persistence, Git, or agent
  invocation was added.
- Unit, contract, architecture, and integration tests use scripted command results, the
  real controlled runner with its finite Python fixture, and `FakeAgentAdapter`. There
  are no live, networked, credentialed, or paid provider calls.

## P3-002 delivered

- Version/top-level-help/subcommand-help-only detection through `CommandRunner`, with
  explicit `AVAILABLE`, `UNAVAILABLE`, and `INCOMPATIBLE` surface results mapped into
  existing P3-001 capabilities and actionable reasons/metadata.
- OpenCode BUILDER-only adapter over a frozen v1 stdin/JSONL surface. Local OpenCode
  remains accurately unavailable; compatible behavior is verified with fake executables.
- Codex REVIEWER and REPAIRER are separate adapters and identities. Installed
  `codex-cli 0.146.0-alpha.3.1` proves JSONL/stdin/ephemeral/ignored-user-config and
  `read-only`/`workspace-write` surfaces. Review disables approval and is read-only;
  repair additionally requires explicit constructor plus typed request authorization.
- Deterministic bounded prompts carry correlation/objective/scope/reference metadata but
  no repository contents or environment values. Model IDs cannot inject options and no
  arbitrary provider argument passthrough exists.
- Strict known-event JSONL parsing rejects malformed/duplicate/truncated/unknown/missing/
  contradictory streams before P3-001 schema/correlation/role/semantic parsing. Provider
  paths do not become Revanent artifacts or approval evidence.
- Every process uses the controlled-command port. Existing cwd, executable, environment,
  stdin, timeout, cancellation, redaction, and output limits remain authoritative. Typed
  command failures preserve unavailable/provider/launch/timeout/cancellation/artifact
  distinctions and write-mode side-effect ambiguity without rollback.
- Finite fake OpenCode/Codex executables prove all roles, exact arguments/cwd, reviewer
  read-only versus repair write mode, selected child environment, and secret redaction.
  Default tests contain no live/model/provider call.

## P3-001 delivered

- Immutable strict version-1 provider-neutral capabilities, requests, responses,
  role-specific payloads, diagnostics, reported usage, failures, statuses, agent artifact
  references, stable invocation/attempt/provider/adapter/scenario IDs, and `AgentAdapter`.
- Explicit `BUILDER`, `REVIEWER`, and `REPAIRER` authority. Review is read-only; builder
  and repairer write/repair requirements are locally validated and never inferred from
  model names.
- Exact response correlation over invocation, run, work package, attempt identity/number,
  role, and expected schema, followed by separate role/request semantic validation.
- One-MiB-or-lower preparse bounds, strict UTF-8 and JSON, duplicate/trailing/nonstandard-
  number/depth/item rejection, strict canonical model validation, known-value redaction,
  and sanitized `INVALID_OUTPUT` without raw payload echo.
- Bounded relative agent artifact references below a separately approved root identity;
  raw references must be redacted. No artifact store or unredacted persistence was added.
- Immutable finite fake scenarios with exact request SHA-256, explicit UTC timing,
  deterministic timeout/cancellation checkpoints, typed or raw outcomes, finite
  exhaustion, isolated counters, serialized concurrency, and replay-by-reinstantiation.
- Architecture and adversarial tests prove that fake/provider claims cannot mutate
  `Run`, construct `ApprovalGate`, execute providers/processes/network/Git, or bypass
  later validation.

## P2-002 delivered

- Immutable version-1 Git repository/status/worktree/request/result/ownership/error
  contracts and provider-independent `GitRepository` port.
- A local Git adapter whose complete process surface uses the P2-001 `CommandRunner`.
- Canonical common-repository identity, exact HEAD/base resolution, porcelain-v2/NUL
  status, NUL worktree registry, local upstream/default branch, and operation markers.
- Configurable exact/pattern/default protected-branch policy and dedicated `revanent/`
  owned branches; protected bases are allowed without direct protected-branch mutation.
- Clean-source creation with collision/race rechecks, hook/fsmonitor/config controls,
  external checkout-filter refusal, post-creation live identity/path/branch/HEAD proof,
  and no original-worktree mutation.
- A dedicated bounded JSON ownership store with stable IDs, exclusive locks, atomic
  writes, strict schema validation, `CREATING`/`ACTIVE`/`PARTIAL`/`REMOVED` states, and
  retained partial/cleanup evidence.
- Conservative cleanup authorized only by record plus matching live Git metadata,
  contained path, base ancestry, and clean/unlocked/operation-free state with no ignored
  files. Only normal worktree removal is used; branches and records remain.
- Real temporary-repository Windows integration coverage for dirt/conflicts/operations,
  special paths, junction escape, collisions/concurrency, tamper/replacement/staleness,
  partial creation, hooks/filters, cleanup races, and source preservation. POSIX branches
  are present in the Windows/Linux CI matrix.

## P2-002 verified commands (P3-001 baseline)

- Pre-edit P2-001 baseline: all canonical gates passed; 188 tests passed with no skips.
- `uv sync --dev` - success, 27 packages resolved and audited.
- `uv run ruff format --check .` - success, 91 files already formatted.
- `uv run ruff check .` - success, all checks passed.
- `uv run mypy src tests` - success, 50 source/test files, no issues.
- Focused P2-002 suite - success, 80 passed and 1 platform-specific skip on Windows.
- `uv run pytest tests/unit tests/contract tests/integration` - success, 268 passed and
  1 platform-specific skip.
- `uv run pytest` - success, 268 passed and 1 skipped on Windows CPython 3.12.11.
- `uv run revanent doctor` - success; Python, Windows platform, uv, Git 2.54.0.windows.1,
  and Codex available; OpenCode accurately unavailable.
- Security scans - no Git subprocess bypass, shell-enabled/string command, public raw
  Git argument forwarding, force/reset/clean/push/merge/commit/prune/branch-deletion
  execution, unowned tree deletion, prefix containment, raw environment forwarding,
  credential assignment, unbounded stream read, or unbounded wait. Git subprocesses
  remain in the sole P2-001 adapter. The only new `unlink` is guarded removal of an
  owned `.lock`/`.tmp` file in the validated ownership root.
- `git diff --check` - success; Git reported LF-to-CRLF normalization warnings only.

These are the final post-documentation gate results.

## P3-001 verified commands

- Pre-edit canonical baseline - success: format, Ruff, mypy, doctor, and full tests;
  268 passed and 1 platform-specific Windows skip.
- `uv sync --dev` - success; 27 packages resolved and audited.
- `uv run ruff format --check .` - success; 104 files already formatted.
- `uv run ruff check .` - success; all checks passed.
- `uv run mypy src tests` - success; 62 source/test files, no issues.
- Focused agent suite - success; 68 passed in 0.36 seconds.
- `uv run pytest tests/unit tests/contract tests/integration` - success; 336 passed and
  1 platform-specific Windows skip in 143.56 seconds.
- `uv run pytest` - success; 336 passed and 1 platform-specific Windows skip in
  148.21 seconds on Windows AMD64, CPython 3.12.11.
- `uv run revanent doctor` - success; Python, Windows platform, uv, Git
  2.54.0.windows.1, and Codex available; OpenCode accurately unavailable.
- Focused security scans - no provider/process/network imports, unsafe deserialization,
  unchecked construction, public `Any`/dictionary agent-port boundary, raw exception/
  payload leakage, credential-shaped literals, workflow/approval authority, fake
  filesystem/process mutation, nondeterministic clocks/IDs/waits, or unbounded script.
  Matches were limited to typed internal JSON dictionaries and static validation text.
- `git diff --check` - success; Git reported LF-to-CRLF normalization warnings only.

These are the final post-documentation P3-001 gate results.

## P3-002 verified commands

- Pre-edit P3-001 baseline - success: sync, format, Ruff, mypy, doctor, and full tests;
  336 passed and one genuine Windows platform skip.
- `uv sync --dev` - success; 27 packages resolved and audited.
- `uv run ruff format --check .` - success; 112 files already formatted.
- `uv run ruff check .` - success; all checks passed.
- `uv run mypy src tests` - success; no issues in 69 source/test files.
- Focused P3-002 unit/contract/integration suite - success; 42 passed in 2.54 seconds.
- `uv run pytest tests/unit tests/contract tests/integration` - success; 373 passed and
  one Windows-only skip in 148.87 seconds.
- `uv run pytest` - success; 373 passed and one Windows-only skip on Windows AMD64,
  CPython 3.12.11, in 148.14 seconds.
- `uv run revanent doctor` - success; Python 3.12.11, uv 0.7.13, Git
  2.54.0.windows.1; OpenCode unavailable; Codex `0.146.0-alpha.3.1` review and repair
  surfaces compatible from safe version/help inspection.
- Focused security scans - no provider subprocess/network/Git/orchestration imports,
  shell execution, arbitrary extra arguments, login/auth, raw host environment, approval/
  state mutation, Git mutation/publication, permissive JSON recovery, or live model test.
- `git diff --check` - success; only Git line-ending normalization warnings, if any.

No actual OpenCode or Codex model invocation occurred. Provider execution evidence is
fake-executable integration; installed Codex evidence is version/help-only detection.

## P4-001 verified commands

- Pre-edit P3-002 baseline - success: sync, format, Ruff, mypy, doctor, and full tests;
  373 passed and one genuine Windows platform skip in 148.68 seconds.
- `uv sync --dev` - success; 27 packages resolved and audited.
- `uv run ruff format --check .` - success; 125 files already formatted.
- `uv run ruff check .` - success; all checks passed.
- `uv run mypy src tests` - success; no issues in 81 source/test files.
- Focused P4-001 unit/contract/architecture/integration suite - success; 107 passed in
  2.36 seconds.
- `uv run pytest tests/unit tests/contract tests/integration` - success; 480 passed and
  one Windows-only skip in 153.89 seconds.
- `uv run pytest` - success; 480 passed and one Windows-only skip in 154.45 seconds on
  Windows AMD64, CPython 3.12.11.
- `uv run revanent doctor` - success; Python 3.12.11, Windows AMD64, uv 0.7.13, Git
  2.54.0.windows.1; OpenCode unavailable; Codex `0.146.0-alpha.3.1` review and repair
  surfaces compatible from safe version/help inspection.
- Focused architecture/security scans - no validation/gate subprocess, network, concrete
  command/provider/Git/storage/CLI/orchestration import; shell execution; arbitrary extra
  arguments; raw host environment; unchecked construction; transition/run mutation;
  agent invocation; persistence; retry; Git mutation/publication; nondeterministic clock/
  ID/wait; provider-created approval; or provider-controlled local gate boolean. The only
  production subprocess import remains the P2-001 local adapter, and the only P4-001
  `ApprovalGate` constructor is the intended local review gate.
- `git diff --check` - success; Git reported only expected LF-to-CRLF normalization
  warnings for the cumulative working tree.
- All P4-001 provider evidence is simulated or uses the finite local command fixture.
  No OpenCode/Codex model invocation or network access occurred.

## P4-002 verified commands

- Pre-edit P4-001 baseline gates: sync, format, Ruff, and mypy passed; the interrupted
  P4-002 tree exposed one terminal-replay E2E defect (501 passed, one failed, one Windows
  skip), which was repaired without weakening earlier contracts.
- `uv sync --dev` - success; 27 packages resolved and audited.
- `uv run ruff format --check .` - success; 136 files already formatted.
- `uv run ruff check .` - success; all checks passed.
- `uv run mypy src tests` - success; no issues in 91 source/test files.
- Focused P4-002 unit/contract/integration/E2E paths - success; 85 passed with no skips.
- `uv run pytest tests/unit tests/contract tests/integration tests/e2e` - success;
  537 passed and one genuine Windows-only filename skip.
- `uv run pytest` - success; 537 passed and one genuine Windows-only filename skip on
  Windows AMD64, CPython 3.12.11.
- `uv run revanent doctor` - success; Python 3.12.11, Windows AMD64, uv 0.7.13, Git
  2.54.0.windows.1; OpenCode unavailable; installed Codex review/repair surfaces compatible
  from version/help-only inspection.
- `git diff --check` - success; only expected LF-to-CRLF normalization warnings for the
  cumulative uncommitted worktree.
- Architecture/security scans found no orchestration subprocess, shell, network, concrete
  provider/Git/SQLite/CLI dependency, raw host environment, destructive/publishing Git,
  provider-created approval, duplicated transition table, unbounded loop, automatic
  ambiguous-write replay, cleanup, live provider call, or nondeterministic ID/clock.
- All orchestration evidence is fake-agent, controlled-command, temporary-SQLite, and
  existing temporary-real-Git test evidence. No OpenCode/Codex model call, credential,
  network access, paid call, commit, push, merge, or publication occurred.

## Known limitations

Orchestration is a library service; P6 still owns user-facing run/resume/status/report
commands and safe configuration wiring. P5 context selection and usage/cost telemetry are
not implemented. The supplied `LocalEvidenceCollector` remains a construction-time port;
P6 must wire a reviewed production collector. Agent artifacts have no general run artifact
store. A durable intent cannot prove whether an external program launched before a host
crash, so ambiguous mutating attempts are preserved and blocked for human recovery rather
than replayed. Exactly-once external execution is not claimed.

OpenCode is absent locally and its supported surface is fake-verified only. Codex help
flags reduce authority but do not prove operating-system sandboxing. No live/provider-model
execution has been tested. Known-value redaction cannot discover unknown, encoded, split,
or transformed secrets. Provider-returned artifact paths are intentionally rejected.
Validation overflow artifacts lack a content digest and their inspection cannot eliminate
a same-user replacement race.

The SQLite and Git repositories remain library primitives; no CLI selects the database,
constructs Git policy, or implements run/resume/status. Worktree ownership remains a
separate versioned state directory but is now bound to orchestration by run/worktree ID and
live verification before use.
Partial records and stale lock files require human recovery. Branches survive cleanup by
design; no automatic branch lifecycle exists.

Git identity is local correlation evidence, not a cryptographic UUID. A malicious same-
user process could rewrite both ownership and matching Git metadata. Root identity changes
after an unrelated-history merge. Path checks, atomic record writes, Git locks, and
post-verification cannot eliminate concurrent filesystem replacement races. Dirty source
repositories and locally configured external checkout filters are conservatively refused.

UNC Git/worktree/state roots are rejected by default; explicitly authorized UNC operation
was not locally tested. Windows Win32 cannot create tab/newline filename components, so the
POSIX-only filename test skips locally. POSIX paths, symlinks, and filename cases are in the
portable CI suite, but remote CI has not been run in this local session. The controlled
runner is not a sandbox and Windows descendant-process termination remains direct-child
only. OpenCode is not installed; live provider integration remains untested and out of
scope until later packages.

## Blockers

None for starting P5-001. OpenCode remains unavailable locally but is an accurately typed
optional capability state, not a blocker to deterministic context-package implementation.

## Architectural decisions

- ADR-0001: Python 3.12+, uv, typed local CLI and adapter boundaries.
- ADR-0002: SQLite current state/events plus versioned file artifacts.
- ADR-0003: Immutable versioned domain/config schemas and central state transitions.
- ADR-0004: Controlled local command port, explicit policies, bounded/redacted adapter.
- ADR-0005: Common-identity/live-verified owned worktrees, separate atomic ownership
  records, preserved partial states/branches, and non-force cleanup.
- ADR-0006: Strict versioned agent envelope, sanitized parser/correlation boundary,
  relative agent artifact references, and finite deterministic fake scenarios.
- ADR-0007: Frozen provider CLI surfaces, strict JSONL framing, and permission-separated
  OpenCode builder/Codex reviewer/repair adapters.
- ADR-0008: Immutable validation evidence, deterministic aggregate replay, separately
  owned local approval facts, and pure fail-closed review-gate decisions.
- ADR-0009: Append-only attempt evidence, revision-guarded at-most-once initiation,
  explicit reconciliation, bounded deterministic repair selection, and local authority.

## Next recommended work package

P5-001 - Deterministic Context Selection and Manifest. Use GPT-5.6 Terra at high reasoning
to add bounded, deterministic, secret-aware context manifests behind provider-neutral
ports while preserving the completed P4 durable coordinator. Use Sol review for path/scope,
secret-exclusion, ordering, truncation, and prompt-injection boundaries.

## Exact next-session bootstrap instruction

Continue Revanent from the completed and verified Phase 4/P4-002 baseline. Read
`AGENTS.md`, authoritative architecture/security/testing/state documents, ADR-0006
through ADR-0009, and `docs/work-packages/P5-001-context-packages.md`; inspect and preserve
the cumulative Git state, then verify the documented 537-pass/one-Windows-skip baseline.
Execute P5-001 only: implement deterministic bounded context selection and a versioned
manifest, enforce repository scope and secret exclusion, integrate through provider-neutral
contracts without weakening P4 orchestration, and add fake-first adversarial tests. Do not
add usage telemetry, user-facing run/resume/report CLI, live providers/network calls,
destructive Git, push, merge, or publication.
