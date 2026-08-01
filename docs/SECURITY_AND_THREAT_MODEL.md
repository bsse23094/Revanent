# Security and Threat Model

## Assets and actors

Assets include user source/history, credentials, worktree integrity, approval
evidence, run state, and host availability. Actors include the user, Revanent,
repository content, Git, external executables, OpenCode/local models, Codex, and any
explicitly enabled network service.

## Principal threats

- Prompt or configuration injection broadens scope or extracts secrets.
- Shell/path injection executes unintended commands or escapes the repository.
- A provider edits forbidden paths or claims actions it did not perform.
- Symlink/junction and case-normalization differences bypass path checks.
- Inherited environment or logs disclose credentials.
- Timeout, crash, or concurrent resume duplicates side effects or corrupts state.
- Friendly but malformed review output bypasses approval gates.
- Git cleanup destroys untracked/user work or targets a non-owned worktree.

## Controls

P2-001 makes the command port the sole production process-launch boundary. Requests
carry a simple configured executable name, an ordered argument tuple, an explicit
working directory, a bounded selected environment, runner-specific resource ceilings,
and optional cancellation/artifact policy. The local adapter passes an argument list
with `shell=False`; it never accepts a command string. Ordered absolute executable
candidates prevent alternative-path selection, and host PATH construction ignores
empty, relative, UNC, and excluded repository entries.

Paths use resolved `pathlib` identities and structural containment rather than string
prefixes. Existing working directories must remain inside approved roots. Relative
operations reject absolute paths and `..`; link, junction, drive/case, sibling-prefix,
UNC, and artifact-directory boundaries fail closed unless explicitly authorized.

Child environments start from a bounded explicit baseline, not `os.environ`, then
apply only allowlisted overrides. Windows environment names normalize
case-insensitively. Credential-shaped keys are forbidden unless explicitly authorized;
authorized sensitive values join configured exact secrets in central redaction.
stdout/stderr are independently drained and bounded. Public output and atomic overflow
artifacts use replacement decoding and redaction before persistence; source and
redacted-representation truncation are explicit.

Timeout and cancellation terminate and reap the direct child; POSIX additionally
targets the new process group. Policy, availability, cwd, environment, launch,
nonzero-exit, timeout, cancellation, artifact, and internal outcomes are normalized
without external exception text.

P2-002 routes every production Git process through that boundary and exposes only
typed repository/worktree contracts. Inspection uses bounded machine-readable/NUL
formats. Git commands are limited to discovery, status/registry/ref/config inspection,
normal dedicated worktree creation, ancestry checks, and normal owned-worktree removal;
there is no arbitrary argument pass-through. Force, reset, clean, prune, push, fetch,
pull, merge, rebase mutation, commit, remote/config mutation, and branch deletion paths
are absent.

Repository identity combines canonical common Git metadata, object format, and root
history; ownership records alone never authorize cleanup. Creation requires a clean,
operation-free source and a dedicated non-protected branch, resolves an immutable base,
rechecks collision-sensitive state, and verifies live Git registration/identity after
checkout. State/worktree roots inside the source must already be ignored. System/global
Git config, pagers, editors, prompts, fsmonitor, and hooks are neutralized; configured
local external checkout filters block creation.

Versioned ownership records use validated ID filenames, bounded reads, exclusive lock
files, synced temporary writes, and atomic replacement in a dedicated Revanent state
directory. Lifecycle evidence is retained after failure and cleanup. Cleanup requires
matching live identity/path/branch/registry/base ancestry plus a clean, unlocked,
operation-free worktree with no ignored files. It uses only non-force Git removal,
verifies deregistration, and preserves the branch. Partial/stale/mismatched/unowned
resources remain blocked rather than repaired or removed.

Provider schemas are versioned and parsed strictly. Approval is computed locally from
evidence, never accepted from prose. State/event changes are transactional and resume
reconciles side effects.

P3-001 implements that provider trust boundary before any live provider exists. Agent
requests contain only explicit capability requirements, bounded repository-relative
scope/context/artifact references, an absolute typed workspace reference, timeout,
cancellation reference, routing metadata, and environment-variable names without
values. Reviewer requests cannot grant writes; repair authority must be explicit.

Response parsing checks the byte ceiling before strict UTF-8 decoding and strict JSON.
Duplicate keys, trailing content, NaN/infinity, excessive nesting/items, unknown
versions/fields/enums, invalid IDs/paths/timestamps, correlation mismatches, and
request-policy mismatches fail closed as sanitized `INVALID_OUTPUT`. Parser errors never
echo provider bytes. Explicit sensitive values are redacted recursively before model
validation. Raw output is neither embedded nor persisted by P3-001; only bounded,
redacted relative references under an approved artifact-root identity may cross the
boundary. Retryable failures require proof of no side effect; ambiguous execution uses
`POSSIBLE` plus non-safe retry classification.

The fake adapter uses no provider executable, subprocess, network, Git, filesystem
mutation, callback, random ID, wall clock, sleep, or workflow transition. Scripts are
immutable and finite, requests match canonical SHA-256 signatures exactly, timestamps
are explicit, instances are isolated, and a lock serializes consumption. Provider
claims never construct `ApprovalGate` or mutate `Run`.

P3-002 routes every provider process through `CommandRunner`. The adapters accept only
fixed CLI arguments and validated model identifiers; there is no arbitrary flag escape
hatch. Review disables interactive approval, ignores user configuration, uses ephemeral
state, and requests the locally verified Codex `read-only` sandbox. Repair is a separate
adapter using `workspace-write` and requires both a typed REPAIRER request and explicit
constructor authorization. These flags reduce authority but do not prove OS isolation.

Only explicitly supplied typed environment overrides can reach a provider, and every key
must also appear in the request allowlist. Configured sensitive values are rejected if
found in prompts/arguments and are redacted during command capture and envelope parsing.
Durable overflow is disabled when sensitive values are present. Provider-returned paths
never become Revanent artifact references. Unknown JSONL events, duplicate/malformed or
contradictory terminals, and invalid envelopes fail closed without raw-output echo.
Timeout, cancellation, malformed output, and artifact failure from write modes retain
`POSSIBLE` side effects; adapters never attempt rollback or Git cleanup.

P4-001 treats command and review evidence as untrusted at the next boundary. Validation
plans reject executable paths, shell strings, credential assignments, unknown
environment names, traversal, duplicate commands, unbounded time/output, and advisory
security checks. `ValidationRunner` executes only through `CommandRunner`, validates
result identity and chronology, keeps stdout/stderr separate, and verifies any overflow
artifact's resolved containment, stream, correlation-derived filename, file type, and
reported byte size. Failure prose cannot override status or exit evidence.

The local review gate replays the exact validation aggregate before considering review.
It rejects missing/failed/timed-out/cancelled/blocked/unavailable/invalid evidence,
correlation or chronology mismatch, non-reviewer roles, malformed/nonterminal responses,
raw or truncated review artifacts, write authority, adapter-identity mismatch, duplicate
or high/critical findings, non-approved verdicts, scope/generated/lock/artifact/dirty
failures, and ambiguous side effects. Only local code constructs `ApprovalGate`; provider
payload models forbid such a field. Gate evaluation has no process, agent, persistence,
Git, retry, repair, or state-transition surface.

P4-002 composes those boundaries without granting new provider authority. The
provider-neutral coordinator imports only ports/domain/pure gates and calls the existing
state machine for every transition. Before each external effect it verifies durable state
and active run-bound worktree identity, appends immutable intent with stable IDs, checks
that the run revision remains current, invokes once, and appends projected normalized
outcome evidence. SQLite migration 2 makes attempt evidence ordered, correlated,
foreign-key owned, unique by stage, and append-only.

A durable outcome is reused after restart; an intent without outcome is never evidence
that no write occurred. Worktree creation may continue only after live ownership proof
matching the durable run, source/target paths, branch, and common repository identity; an
active record owned by another run is incompatible. Incomplete builder, repair, or
validation execution remains ambiguous and automatic replay is blocked. Ambiguous outcomes
also suppress automatic repair and local approval. Cancellation, including an in-flight
reviewer cancellation, preserves partial attempts/worktrees and reaches `CANCELLED`. No
coordinator cleanup, rollback,
commit, reset, clean, push, merge, publication, branch deletion, or raw Git command exists.

Repair authority is chosen by pure local policy from bounded typed evidence. Mechanical
first failures may use only a write-capable builder. Repetition and high-risk evidence
require a repair-capable Codex adapter plus explicit local write authorization. Scope
violation, invalid evidence, exhausted limits, cancellation, or unresolved side effects
forbid repair. Reviewer read-only authority remains separate, and only `ReviewGate` can
construct approval evidence. Architecture and crash-window tests enforce these boundaries.

P5-001 makes context construction a separate provider-neutral trust boundary. Typed local
evidence is the only discovery input; repository or provider text cannot add candidates,
change scope, raise trust, authorize writes/network/publication, suppress validation, or mark
approval. Forbidden TaskSpecification scope wins over required/preferred/optional reasons.
Repository-relative spelling plus resolved structural containment rejects traversal,
absolute/UNC/sibling roots, `.git` and generated/dependency/cache directories, and symlink or
Windows junction escapes. Approved artifacts additionally require typed root, run/package/
correlation, type, completeness, size, redaction, and optional digest agreement.

All content reads use one bounded reader with regular-file checks and before/opened/after
metadata comparison. File changes receive only a small configured retry count; required race
evidence blocks. Redaction precedes retention and digesting. Configured values, authorization
headers, credential assignments, and token URL parameters are replaced; `.env`, common cloud
credential paths, private-key files, and PEM/private-key blocks are refused. Manifests contain
metadata and safe post-redaction digests only, while bodies remain bounded in memory and enter
the existing typed AgentRequest context field. SQLite persists context intent and manifest
evidence, never selected bodies.

P5-002 treats usage and limits as local authority. Repository content, provider prose, and
provider output cannot change budgets or provenance. Atomic SQLite reservation includes
settled usage and active reservations before launch; settlement appends usage and lifecycle
evidence in one transaction. Stale revisions write nothing. Unknown metrics remain
`UNAVAILABLE`, never numeric zero, while ambiguous execution retains an `UNRESOLVED`
reservation without automatic replay, release, or time expiry.

Telemetry persistence is metadata-only: bounded identities, metric values/units, provenance,
provider/model identifiers, correlations, timestamps, stable reason codes, and reservation
lifecycle. Contracts have no prompt, context-body, source, raw provider/command output,
credential, authorization-header, environment-value, home-path, or host-metadata fields.
Provider-reported tokens cannot be relabelled after persistence. Decimal estimates require
currency and estimator identity and are never called actual, billed, or charged cost. There
is no network pricing lookup or bundled current rate table.

## Residual risks

The intent-before-launch protocol cannot distinguish a crash immediately before process
launch from a crash immediately after possible writes. Revanent therefore claims durable
at-most-once initiation plus fail-closed reconciliation, not exactly-once external
execution. P6-002-C1 exposes that recovery state through typed resume/status/cancel workflows;
resume never replays ambiguous mutating work and status is read-only. Migration 5 binds every
runtime Run to immutable full repository identity plus deterministic worktree path/ID/branch
evidence. Runtime operations rediscover that identity and verify live active ownership before
recovery; sibling-prefix, replacement, link/junction, wrong-repository, and wrong-worktree cases
fail closed without reconciliation or cleanup. Task files are bounded regular no-follow inputs;
their prose never becomes command, network, publication, repair, or approval authority. Status
projects no bodies and reports contradictory evidence without repairing it. SQLite
revision guards stop cooperative coordinators but are not a distributed lock against a
malicious same-user process. The small interval between the final state/worktree check and
external launch remains a check/use race; stable evidence explains it but cannot remove it.
SQLite `BEGIN IMMEDIATE` serializes supported local writers; it is not distributed
coordination. Provider hard stopping cannot be guaranteed when a CLI lacks a finite token or
cost ceiling, so a configured hard external budget fails closed before invocation.

P6-002-C1 production composition deliberately supplies conservative local approval evidence with
`scope_justified=False`. Consequently a production run cannot fabricate approval and may block even
when provider review is favorable. P6-002-C2 or later may add a reviewed collector for scope,
generated/lock files, artifacts, and cleanliness, but must not infer those facts from provider prose
or telemetry. Fake/local tests can reach `APPROVED` only with explicitly injected complete evidence.

Command artifact verification is not cryptographic and remains subject to replacement
after inspection by another same-user process. Review read-only flags reduce authority
but do not establish an operating-system sandbox.

An explicitly authorized executable can use every host permission available to it,
launch descendants, invoke other programs, use the network, or race filesystem checks.
The MVP is not a security sandbox. Windows standard-library termination guarantees the
direct child only; POSIX process groups do not provide namespace isolation. Windows may
route explicitly authorized `.cmd`/`.bat` files through its command processor even
though Revanent does not set `shell=True`. Link/junction resolution cannot eliminate a
malicious replacement between validation and use.

Redaction guarantees removal of configured exact values, authorized sensitive
environment values, supported authorization headers, and common credential-shaped
assignments from public command results and artifacts. It cannot identify every secret
or prevent a tool from encoding/splitting a value beyond those patterns. Network denial
and filesystem scope enforcement still depend on the execution environment. Live
adapters require adversarial review before release.

Known-value agent redaction likewise cannot discover encoded, split, transformed, or
unknown secrets; live adapters must combine it with controlled-command redaction and
minimal context/environment selection. Relative agent artifact references do not prove
that a file exists or is safely stored; a future artifact store must authorize the
root, write redacted bytes, and verify integrity. In-memory fake replay does not
reconcile durable side effects or concurrent processes.

Context redaction likewise cannot detect every encoded, fragmented, encrypted, transformed,
or unknown secret. File metadata checks do not provide snapshot isolation and cannot prevent
a same-user replacement after the final check. SHA-256 fields provide integrity correlation,
not signatures or actor authentication. Python-only import discovery and exact-name test
mapping can omit custom relationships, which must be supplied as explicit typed evidence.

Git and filesystem identity have no cryptographic repository UUID. A malicious process
with the same host-user authority can rewrite both owned records and matching Git
metadata. Root-history identity deliberately changes after an unrelated-history merge.
Atomic records and Git locks reduce but do not eliminate check/use races, including a
worktree path replacement immediately before normal removal. Stale lock files require
manual recovery. UNC repositories/worktree roots are rejected by default; explicitly
authorized UNC operation and POSIX branches were not executed in the local Windows
completion environment. Repository-configured external checkout filters are refused,
which is safer but excludes some legitimate filter-based repositories.

## P6 initialization and diagnostic boundary

Project YAML, target paths, provider help/version output, and the host environment remain
untrusted. P6 accepts only a bounded regular root-level configuration through the existing safe
loader, retains no secret values, and has no general environment overlay. Initialization rejects
links/junctions, path escapes, `.git` destinations, unignored in-tree owned roots, conflicting
or special files, and unexpected owned-root entries. It uses no-clobber creation and never edits
`.gitignore`, Git configuration, branches, commits, worktrees, or run state.

Doctor and provider detection use a filtered baseline environment and controlled executable
allowlist with repository-local paths excluded. They render only bounded normalized version and
reason facts. They do not use credentials, invoke a model, access a network, trust raw provider
output, or print tracebacks for typed failures. Strict mode changes exit semantics only; it does
not grant provider authority.

## P7 live-certification authorization

Installed credentials or executable presence never selects a live test. Default pytest excludes the
`live` marker. Explicit selection additionally requires a certification switch, exact acknowledgement,
one role-specific model, and finite call/time/token/cost ceilings. Production runtime requires
separate network, OpenCode-builder, Codex-reviewer, and Codex-repair-write decisions. Provider stdin
is disabled in the shared controlled runner unless that composition explicitly authorizes it.

Live fixtures exist outside the Revanent checkout and use an isolated source repository plus owned
worktree. Certificate metadata excludes prompts, bodies, output, environment, credentials, and raw
errors. Provider-managed networking remains outside Revanent; no direct HTTP, SDK, authentication,
telemetry upload, pricing, or Git publication surface was added.

## P6-002 evidence-report boundary

Report requests are read-only inspection. The assembler may read canonical durable evidence but may
not transition, reconcile, settle, invoke a provider/validator, mutate Git/worktrees, create
approval evidence, or repair storage. It preserves contradiction reason codes and refuses to call
an APPROVED Run complete without independent ApprovalGate, validation, review, correlation, and
ambiguity evidence.

JSON is bounded canonical metadata and Markdown is an escaped projection of it. Neither includes
task/context/source/provider/command bodies, credentials, raw environment values, authorization
headers, raw exceptions, or raw output. Explicit report output is confined to the configured report
root; traversal, absolute, `.git`, link/reparse, special, and differing-collision paths are refused.
The SHA-256 digest is integrity metadata, not a signature. Local atomic creation reduces but cannot
eliminate host-authority check/use races; report output never gains authority over workflow state.
