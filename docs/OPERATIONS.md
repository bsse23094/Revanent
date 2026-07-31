# Operations

## Agent contracts and deterministic fake

P3-001 exposes a library-level `AgentAdapter`; P4-002 orchestration invokes it, but no CLI
does. Callers construct a validated `AgentRequest` with stable invocation/run/work-
package/attempt correlation, one role, explicit read/write and feature requirements,
bounded scope/context, a typed workspace reference, timeout, optional cancellation
reference, artifact policy, routing metadata, and environment-variable names only.
Passing a live cancellation token occurs separately through `invoke`; values and the
host environment never belong in the request.

Adapters must publish validated capabilities and reject unavailable providers,
unsupported roles, access modes, structured output, timeout, cancellation, usage,
artifact, or repair requirements before execution. `COMPLETED`, `FAILED`, `BLOCKED`,
`TIMED_OUT`, `CANCELLED`, `INVALID_OUTPUT`, and `UNAVAILABLE` are distinct. Failure
categories, retry disposition, and `NONE`/`POSSIBLE`/`CONFIRMED` side-effect state must
agree. Provider file, command, and review values are claims for later verification, not
state transitions or approval.

Provider envelopes must go through `normalize_agent_output`. Its configured maximum is
one MiB and can only be lowered. It strictly parses one UTF-8 JSON object, applies depth
and collection limits, redacts explicitly supplied sensitive values, validates the
immutable envelope, then checks correlation and request-specific semantics. Rejected
bytes become sanitized `INVALID_OUTPUT`; do not log the original bytes. P3-001 does not
write raw output. A raw-output reference is allowed only when the caller policy permits
it and the referenced bounded artifact is already redacted below the approved root.

`FakeAgentScenario` is immutable and finite. Construct each step with the request's
`agent_request_digest`, explicit UTC start/duration, optional bounded cancellation
checkpoints, and a typed or bounded raw outcome. Reuse of an adapter continues consuming
its ordered sequence; exhaustion is explicit. Recreate the adapter from the same
scenario to replay from step zero. No durable idempotency cache, sleep, provider probe,
network, subprocess, repository mutation, or artifact write occurs. Fake results must
always be labeled simulated, never live.

P3-002 adds library-level OpenCode builder and separate Codex reviewer/repairer adapters;
there is still no user-facing `run` CLI. P4-002 can invoke adapters supplied explicitly to
its library service. Construct live adapters only with a controlled
runner whose executable/path/environment/redaction policies match the request. Detection
is safe version/help inspection: `AVAILABLE` means the exact frozen CLI surface was
proved, `UNAVAILABLE` means no usable executable, and `INCOMPATIBLE` means an installed
surface failed closed. The doctor reports compatibility, not mere PATH presence.

Reviewer uses Codex `exec` with noninteractive approval, ignored user configuration,
ephemeral state, JSONL, stdin, and `read-only`. Repair is a different adapter/argument
path, uses `workspace-write`, and requires `write_authorized=True` plus a REPAIRER request.
OpenCode accepts BUILDER only and requires a verified `run --format json` stdin surface.
All modes require an existing typed WORKTREE cwd. No adapter commits, pushes, cleans,
resets, approves, changes run state, or verifies provider file/command claims.

Provider output is bounded/redacted by the runner, then its exact JSONL grammar and the
P3-001 envelope are parsed strictly. Do not retry a write-capable timeout, cancellation,
launch uncertainty, malformed terminal, or artifact failure as side-effect-free: inspect
the owned worktree later. Provider-returned paths are not trusted artifacts. Exact secret
values must be configured in both runner redaction and parser settings; overflow artifacts
are suppressed when such values are present. Live/model calls remain prohibited by
default and belong to explicit P7-001 opt-in coverage.

## Controlled commands

P2-001 exposes a library-level `CommandRunner`; no run/validation/provider CLI workflow
uses it yet. Callers must construct explicit `CommandPolicy`, `ExecutablePolicy`,
`PathPolicy`, `EnvironmentPolicy`, and `Redactor` instances. An executable is a simple
configured capability name. Its ordered absolute candidate paths are policy-controlled;
which candidate currently exists and its final resolved identity are environment-
dependent. Never build policy from an unfiltered repository-controlled PATH. When host
PATH discovery is necessary, exclude the target repository and other writable roots.

Working directories must already exist under an approved resolved root. Artifact
directories must already exist under separately configured artifact roots. The runner
does not create broad directories. Give every command a correlation identifier unique
within its owned artifact directory; stable `<id>.stdout.log` and `<id>.stderr.log`
names are atomically replaced. Artifacts are created only for source or sanitized-
representation overflow, are redacted before their first durable write, and report
complete/truncated byte accounting. A write failure is an explicit terminal result.

Environment policy receives selected baseline values only. Do not pass `os.environ`.
Authorize PATH, PATHEXT, COMSPEC, interpreters, credential-shaped variables, batch
launchers, stdin, and artifacts only for a concrete adapter need. Authorized sensitive
values must remain in the redaction set. Diagnostic surfaces should use `CommandResult`,
not request internals or raw process exceptions.

Timeout begins immediately before launch. Cancellation before launch is side-effect
free; cancellation observed during execution wins a same-iteration race with timeout.
POSIX terminates the new process group. Windows terminates/reaps the direct child only;
operators must not treat this as descendant isolation. Preserve failed owned artifacts
for diagnosis, but do not claim unredacted full output exists after its configured cap.

The environment doctor performs `uv`/Git version probes and provider version/top-level/
subcommand help probes through this runner. It explicitly permits Windows executable/
batch suffixes needed for installed CLI launchers and forwards only selected basic
process variables. Provider help inspection submits no model prompt.

## Validation and local review gates

P4-001 provides library primitives; P4-002 orchestration invokes them, but no CLI does.
Construct
a version-1 `ValidationPlan` only from locally approved command capabilities, workspace,
artifact root, and environment names. Every plan needs a required command. Required
failures prevent approval. Advisory failures remain visible and are tolerated only when
`allow_advisory_failures` is explicit and all required evidence is complete. Fail-fast
is explicit; later commands become `NOT_RUN`. Cancellation always prevents later launch.

The normalization map is: expected successful exit `PASSED`, unexpected exit `FAILED`,
timeout `TIMED_OUT`, cancellation `CANCELLED`, missing executable `UNAVAILABLE`, policy
or launch refusal `BLOCKED`, malformed/internal/artifact evidence `INVALID`, and skipped
commands `NOT_RUN`. Do not infer status from stdout/stderr. Retained output is bounded;
complete-output policy requires complete redacted artifacts when capture truncates.

Call `ReviewGate.evaluate` only after validation and a P3 REVIEWER response exist. Supply
locally observed scope, generated-file, lockfile, artifact, repository-cleanliness,
read-only, adapter-identity, and side-effect facts after both validation and review have
completed. `APPROVABLE` contains a satisfied local `ApprovalGate`; every other status has
reason codes and no gate. P4-001 does not itself persist this decision or transition a
run; P4-002 performs that wiring.

## Bounded orchestration and repair

P4-002 exposes a library `OrchestrationService`; P6-002 still owns user-facing
run/resume/status/report commands. Construct the service only with one `RunRepository` and
matching `OrchestrationJournal`, a `GitRepository`, permission-separated agent adapters,
`ValidationExecutor`, `ReviewGate`, reviewed `LocalEvidenceCollector`, UTC clock, and stable
ID factory. Do not pass concrete SQLite/provider/Git implementations into domain models.

An `OrchestrationRequest` supplies the existing durable run ID, optional entry revision,
one run-bound worktree request, bounded builder/reviewer/local-repair and optional Codex-
repair prototypes, ordered validation plans, and explicit Codex write authorization. It is
not project configuration and cannot enable cleanup, publication, or arbitrary commands.
Context remains a prebuilt bounded P3 reference until P5.

Execution is finite. Before an external effect, the coordinator checks duration,
cancellation, current state/revision, limits, and live active worktree ownership; appends a
stable intent; rechecks currency; invokes once; and appends projected outcome. On restart,
use a request whose expected revision matches the current nonterminal run, or deliberately
omit the entry check. A terminal run returns idempotently even when the original entry
revision is old. An outcome persisted before its transition is reused.

Do not call `execute` to guess through an incomplete intent. Call library `reconcile` after
operator/Phase-6 selection. A worktree-creation intent may advance only when live ownership
matches. An incomplete builder/repair/validation boundary is ambiguous, is recorded, and
blocks without reinvocation. Preserve its worktree. Exactly-once execution is not claimed;
the crash window between durable intent and external launch is intentionally fail closed.

Repair decisions are local and durable. A first narrow mechanical defect may use the
write-capable local builder. Repeated, malformed-output, or high-risk evidence requires a
repair-capable Codex adapter and explicit authorization. Scope violation, invalid evidence,
cancellation, unresolved effects, external prerequisites, or exhausted limits prevents
repair. Every mutating repair returns through complete validation; no repair or reviewer
can approve.

`FAILED` represents invalid/internal or exhausted execution where the state table permits
it. `BLOCKED` represents missing tools/providers, ownership/recovery ambiguity, absent
authority, external prerequisites, and early-phase duration exhaustion where the
authoritative state table has no FAILED edge. `CANCELLED` is entered only through the state
machine after partial evidence is preserved; a reviewer cancellation response takes
precedence over the review gate's blocked classification. During workspace-intent
reconciliation, active ownership must exactly match the durable run, source/target paths,
branch, and common repository identity. Never edit attempt rows or ownership records to
unblock continuation.

## Safe Git worktrees

P2-002 is a library boundary; no CLI creates or removes a worktree yet. Construct a
`LocalGitRepository` with the shared controlled runner, its matching `PathPolicy`, an
already-existing approved worktree root, an already-existing dedicated ownership-state
directory, and optional `ProtectedBranchPolicy`. The runner must authorize the simple
`git` capability, an eight-MiB bounded output ceiling, a 60-second-or-lower timeout, and
only the Git environment override keys emitted by the adapter. Do not seed its explicit
baseline with repository override variables such as `GIT_DIR` or `GIT_WORK_TREE`.

UNC source/worktree/state paths are rejected by default. Enabling them requires explicit
authorization in both path and Git/ownership policies and remains outside local P2-002
verification. Working/state roots may live outside the source repository. If either is
inside it, the containing path must already be ignored; Revanent will not edit
`.gitignore` or tolerate newly introduced source dirt.

The source repository must be non-bare, have a committed HEAD, and be clean with no
merge, rebase, cherry-pick, revert, bisect, sequencer, or conflict state. Creation may
start from `HEAD`, a full local head/tag ref, or an option-safe commit ID. The task branch
must be a valid literal under `revanent/` and must not match exact, pattern, or locally
known default-branch protection. Common defaults are `main`, `master`, `release/*`, and
`protected/*`; callers may replace these rules explicitly. A protected branch may be the
base, but it is never the task-mutation branch.

Git execution disables system/global config, prompts, pagers, editors, fsmonitor, and
repository hooks. Local configured `filter.*.(clean|smudge|process)` checkout commands
refuse creation, so repositories that rely on Git LFS or another external checkout
filter require a future reviewed policy rather than an override. Creation resolves and
records the exact base commit, checks records/paths/refs/registry twice around the race
boundary, performs normal worktree add, and live-verifies identity/path/branch/HEAD.

Ownership files are `<worktree-id>.json`; matching `.lock` and temporary files are
Revanent-owned implementation details. Do not remove a lock or edit a record merely to
unblock automation. `CREATING` or `PARTIAL` means creation was interrupted or could not
be fully verified. Preserve the record, inspect `git worktree list --porcelain`, the
dedicated branch, target path, and repository identity, then make an explicit human
recovery decision. Revanent never force-rolls back a partial resource.

Cleanup revalidates the record against live Git metadata. Any path/identity/branch/HEAD
mismatch, missing/replaced target, stale record, lock, conflict, operation, staged/
unstaged/untracked/ignored file, or Git-locked worktree refuses removal. Authorized
cleanup runs only normal `git worktree remove`, verifies deregistration, keeps the branch
and ownership JSON, then records cleanup HEAD/time as `REMOVED`. An already verified
`REMOVED` record is idempotent. There is no automatic branch deletion, worktree prune,
force retry, reset, clean, stash, commit, push, merge, or publication path.

The record and live Git evidence protect against ordinary collision, staleness, and
replacement, not a malicious same-user process able to rewrite both. A concurrent path
replacement can still occur after verification; normal Git refusal and retained records
are the final fail-safe. Back up the ownership state together with any retained partial
worktree before manual recovery.

## Durable state

P1-002 provides a library-level SQLite repository at an explicit `pathlib.Path`; no
CLI currently selects or initializes that path. Initialization creates the database
file intentionally. Parent creation is disabled by default and must be requested
explicitly. Read operations use SQLite read-only mode and never create an absent file.

SQLite schema version 2 is recorded in `schema_migrations`. Repeated initialization validates
and preserves the existing history. A newer, incomplete, malformed, or corrupt
database is rejected without deletion, truncation, migration downgrade, or automatic
repair. Operators should preserve the file for diagnosis. Future migrations append to
the ordered forward-only migration list and apply in one explicit transaction.

Every operation opens and closes its own connection with a configured busy timeout and
verified foreign-key enforcement. State-transition writes use `BEGIN IMMEDIATE`, an
optimistic run revision, and a stable event idempotency key. Initial creation produces
revision zero and no event. Successful transitions increment the revision and append
one immutable, per-run sequenced event atomically. Equal event timestamps are ordered
by sequence. Migration 2 adds append-only per-run orchestration intent/outcome/
reconciliation records with strict correlation and unique attempt/stage boundaries. Stale
revisions fail without writing; an event/attempt insertion failure rolls back and launches
no later side effect.

Back up the SQLite file only while no writer is active. Multi-process behavior beyond
SQLite writer serialization and revision rejection is not claimed. No CLI resume,
database export, or general durable run-artifact store exists yet. Library worktree
verification and conservative interruption reconciliation exist in P4-002; Phase 6 must
expose them without automatic destructive recovery. P2-001 command overflow references
remain adapter-boundary types; wiring them into a general `.revanent/runs/<run-id>/`
artifact store remains later work.
