# Testing Strategy

Unit tests cover configuration, state transitions, repair/path policy, command
construction, schema parsing, usage accounting, context selection, reports, and
redaction. Contract tests freeze external and persisted schema versions. Integration
tests use fake executables and temporary repositories/worktrees. End-to-end fixtures
cover approval, validation failure, both repair paths, limits, interruption/resume,
forbidden paths, dirty state, and missing dependencies.

SQLite integration tests use real temporary database files, including paths with
spaces. They exercise transactional DDL/DML, constraints, append-only triggers,
foreign keys, rollback after an event insert failure, optimistic concurrency,
idempotent retry, close/reopen reload, and externally simulated corruption. SQLite
behavior under test is not mocked and tests use no sleeps or timing races.

P2-001 command contract/unit/integration tests use the real local adapter with one
deterministic test-only Python executable fixture. They prove literal spaces and shell
metacharacters, explicit executable order and bypass rejection, resolved cwd/path
containment, Windows case and actual junction escape, explicit UNC behavior, filtered
environment precedence, sensitive-key rejection, redaction overlap and expansion
bounds, structured success/nonzero/launch/internal outcomes, direct-child timeout and
cancellation cleanup, deterministic cancellation precedence, separate/truncated
stdout and stderr, simultaneous multi-megabyte drain, invalid-byte decoding, atomic
redacted artifacts, stdin policy, and concurrent invocation isolation. Architecture
contract tests permit production `subprocess` imports only in
`revanent.commands.local` and require literal `shell=False` at both platform launches.

Timeout tests use fixture-created marker files and short polling; they do not use
arbitrary multi-second sleeps. Windows junction creation uses the reparse-point API,
so the core current-platform escape test does not silently skip for symlink privilege.
POSIX uses a directory symlink and process-group termination branch. The same portable
suite is configured for Windows/Linux CI; local completion evidence identifies the
platform actually run.

P2-002 contract/unit/integration tests use Git through the real controlled runner and
temporary repositories with repository-local identity and line-ending configuration;
they never depend on global user identity or network remotes. Contract tests freeze Git
and ownership schema version 1, keep infrastructure out of the port/domain, confine
process execution to the P2-001 adapter, inspect the mutation surface, and prove that
the only package filesystem deletion is a contained owned lock/temporary file.

Parser/unit tests cover strict branch/revision spelling, configurable protection,
porcelain-v2 ordinary/rename/unmerged/special-path records, worktree registry records,
malformed/truncated/undecodable output, status semantics, lifecycle consistency,
bounded atomic record round-trip, collisions, stale locks, schema rejection, and record
filename/ID matching. Integration tests cover non-repository/bare/linked discovery;
branch, detached HEAD, exact base, local upstream/default branch, staged/unstaged/
untracked/ignored/conflict and operation states; protected-base creation; spaces and
Unicode; traversal, sibling-prefix, symlink/junction, ref, target, branch, registry, and
record collisions; concurrent IDs/branches; live identity verification; partial
creation; malformed/tampered/stale/replaced ownership; hook/filter controls; clean,
dirty, ignored, locked, operation-active, unowned, and race-dirtied cleanup; branch and
record retention; and an unchanged original worktree.

Win32 cannot create filename components containing tabs/newlines, so that single
POSIX-only integration case is marked only on Windows. Windows link escape uses the
reparse-point junction helper and does run locally. Linux CI executes the same core Git
suite plus the POSIX filename/symlink branches on Python 3.12 and 3.13; local completion
does not claim that remote CI has already run.

The default suite makes no paid or live provider calls. P3-002 adds no live test. Any
future live OpenCode/Codex test must be explicitly opted in and marked; installed CLI
presence alone can never enable it. Simulated results never count as live evidence.

P3-001 adds agent contract, parser, architecture, and fake-adapter tests. Contract tests
freeze schema version 1 round trips, canonical JSON, immutability, IDs, UTC ordering,
role/access invariants, status/failure/side-effect consistency, explicit reported usage,
relative artifact containment, and duplicate/unknown/missing-field rejection.
Adversarial parser tests cover byte ceilings, invalid UTF-8, trailing content, duplicate
keys, NaN, non-object envelopes, excessive depth/items, unknown versions/enums/fields,
missing fields, malformed success claims, exact correlation, role semantics, and known
secret removal without payload echo.

Fake tests cover builder/reviewer/repairer completion, blocker/provider failure,
unavailable capability, timeout without sleeping, pre- and mid-invocation cancellation,
unsupported role/capability before consumption, exact request mismatch, ordered repeated
steps, exhaustion, malformed/schema/correlation raw output, artifact references,
per-instance isolation, serialized concurrent access, and canonical replay. Architecture
scans keep provider/process/network/Git/SQLite/CLI/orchestration imports, workflow state
mutation, approval construction, arbitrary callbacks, and public `Any` out of the agent
boundary. All P3-001 results are simulated.

P3-002 unit tests freeze compatible/unavailable/incompatible detection, exact OpenCode
builder and distinct Codex read-only/workspace-write arguments, request/role/environment/
repair prelaunch rejection, command-result normalization, strict JSONL events, prompt
bounds, and provider blocker/failure evidence. Integration tests launch finite fake
OpenCode/Codex executables through the real controlled runner on an owned temporary
worktree. They prove all three roles, exact cwd/arguments, reviewer read-only mode,
explicit repair write mode, selected child environment keys, and secret echo redaction.
Architecture scans prohibit provider subprocess/network/Git/orchestration imports,
approval/state mutation, arbitrary extra flags, shell enabling, and Git mutation paths.
Version/help-only inspection of the actual installed CLI is capability evidence, not a
live model test.

P4-001 contract tests freeze validation and gate schema version 1, canonical JSON,
immutability, strict fields/versions, IDs, ordering, command/argument separation,
required/advisory rules, path, timeout, output, environment-name, and credential bounds.
Runner unit tests cover status mapping, expected exits, prose non-authority, order,
fail-fast, explicit `NOT_RUN`, pre/mid/in-flight cancellation, advisory policy, malformed
correlation/chronology/output, artifact escape/accounting, selected environment, and
aggregate replay rejection. Review-gate unit tests cover every local boolean, validation
failure/interruption, reviewer terminal statuses and roles, verdicts, duplicate/severe
findings, plan/run/work-package/invocation/adapter/chronology mismatch, prose approval,
truncated artifacts, deterministic decisions, and provider approval injection.

Integration tests use the real controlled runner with the finite Python fixture and a
`FakeAgentAdapter`. They prove ordered cwd/stream execution, nonzero status authority,
redaction, bounded correlated artifacts, real timeout, and local-only approval. Static
architecture tests prohibit concrete runner/provider/Git/storage/CLI/orchestration,
process/network, transition, retry, and mutation dependencies. No live model or network
test is enabled.

P4-002 contract tests freeze orchestration schema version 1, strict/canonical repair and
reconciliation evidence, UTC/stable identifiers, and the SQLite schema-version-2 journal.
Architecture scans prohibit concrete provider/command/Git/SQLite/CLI/process/network
dependencies, transition duplication, `ApprovalGate` construction, cleanup/destructive Git,
shell execution, and unbounded loops in orchestration. Pure repair-policy tests cover first
mechanical failure, repeated/high-risk/malformed escalation, missing or unauthorized
repair capability, exhausted limits, cancellation, unresolved effects, scope, invalid
evidence, external requirements, reason codes, and determinism.

Fake-first E2E tests use temporary real SQLite databases, `FakeAgentAdapter`, scripted
validation commands, deterministic clocks/IDs, and owned-worktree contract fakes; the
existing P2 suite separately exercises the real controlled-command/Git worktree adapter on
temporary repositories. Scenarios prove direct approval, local repair, authorized Codex
repair, repeated-defect escalation, builder/reviewer/repair/duration limits, missing
validation tooling, invalid evidence, scope and ownership refusal, pre/mid-build
cancellation, in-flight reviewer cancellation precedence, terminal replay, outcome-before-
transition crash reuse for build/validation/review, exact live worktree-intent
reconciliation (including another-run refusal), incomplete mutating-intent refusal, and
journal-insert rollback before external launch. Storage tests prove version-1-to-2 forward
migration preserves runs. The full P4-002 suite is fake-only and makes no provider/model or
network call.

P5-001 contract tests freeze context schema version 1, canonical round trips, unknown-version
and field rejection, metadata/body separation, byte/count/ratio/digest invariants, bounded
exclusion ledgers, AgentRequest projection, and the context/orchestration/storage dependency
boundaries. Unit tests cover source-order-independent reason merging, explicit/change/diff/
governance/validation/review/attempt/decision discovery, bounded Python imports and exact-name
tests, role priority, scope precedence, required/preferred/optional eviction, binary/encoding/
missing/oversize handling, deterministic UTF-8 truncation, race retry/exhaustion, secret
patterns and token URLs, prompt-injection provenance, artifact correlation/integrity, content
aliases, trust-separated deduplication, and exact byte reduction.

Integration uses temporary roots and the real context reader to prove current-platform
symlink or Windows reparse-point junction escape refusal. E2E orchestration tests use the real
selector, temporary SQLite schema 4, fake Git/agents, deterministic clocks, and scripted local
validation to prove CONTEXT_PREPARING intent/outcome durability, metadata-only persistence,
successful context propagation, required-context provider refusal, stale/cancellation
prelaunch behavior, and all prior P4 crash/repair/approval invariants. No live provider,
network or repository Git mutation appears in the suite.

P5-002 contract and architecture tests freeze telemetry schema/provenance/unit rules, reject
raw/sensitive payload fields, distinguish unavailable from zero and unresolved lifecycle,
reject token contradictions, unsupported categories, and float money, and exclude SQLite,
subprocess, Git, network, provider implementations, transitions, and pricing lookup from the
telemetry boundary. Real temporary SQLite integration uses barriers and concurrent threads to
race final attempt, token, duration, and Decimal-cost capacity; identical/conflicting
reservations and settlements; stale revisions; and injected transactional rollback. E2E tests
prove intent/reservation/invocation/outcome/settlement ordering, validation timeout capping,
overage, hard external-budget prelaunch refusal, restart settlement without replay, and durable
unresolved recovery. All provider evidence remains deterministic fake/local evidence.

Canonical local and CI gates:

```text
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest
```

CI runs the gates on Windows and Linux with Python 3.12 and 3.13. Security-sensitive
changes require negative tests at the relevant trust boundary. Tests assert actual
behavior and avoid replacing the behavior under test with ceremonial mocks.
