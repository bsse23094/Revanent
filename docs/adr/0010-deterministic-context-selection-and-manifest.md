# ADR-0010: Deterministic context selection and manifest evidence

- **Status:** Accepted
- **Date:** 2026-07-31

## Context

Agent requests need enough task, source, test, governance, validation, review, repair,
and artifact evidence to act correctly without forwarding a repository or raw provider
transcript wholesale. Repository text and provider output are untrusted, filesystem
content can change while read, and a context decision must remain explainable across
orchestration continuation. Provider-owned selection would also let prose acquire scope
or workflow authority.

## Decision

`revanent.ports.context` owns strict immutable schema-version-1 requests, typed evidence,
candidate, item, exclusion, package, manifest, result, and selector protocols. The local
implementation discovers candidates only from explicit task paths, changed/diff paths,
typed validation failures, typed unresolved review findings, prior attempts, repair
decisions, exact governing-document rules, bounded direct Python imports, exact
`test_<module>.py` conventions, and explicitly approved agent artifact references. It
uses no embeddings, vector index, LLM relevance, fuzzy repository search, provider
heuristic, target-module import, subprocess, Git command, or network access.

Forbidden scope overrides every reason. Allowed/forbidden patterns use canonical
repository-relative structural matching; absolute, traversal, sibling-prefix, link,
junction, UNC, excluded-directory, artifact-root, repository/worktree-identity, and
correlation differences fail closed. A single reader owns bounded filesystem access and
compares resolved target plus size, nanosecond modification time, device, inode/file ID,
and opened-file metadata before and after each read. At most two configured retries are
possible; this detects ordinary changes but does not claim snapshot isolation.

Priority is explicit: required control/package/task/failure/high-finding/decision
evidence precedes preferred explicit changes/tests/governance and optional dependencies.
Builder, reviewer, and repairer roles adjust deterministic numeric priority without
changing authority. Required evidence is never evicted or silently truncated. Preferred
and optional evidence uses stable importance, priority, source, reference, and ID ties.
Python dependency depth/count and exact-name test traversal have independent static
bounds. Only UTF-8 regular text is retained; binaries, special files, generated/cache/
dependency directories, and unsupported encodings are excluded.

Configured values, authorization headers, credential assignments, and token-bearing URL
parameters are redacted before public retention. `.env`, common cloud credential paths,
private-key files, PEM/private-key blocks, and unsafe required credential material are
refused. The policy is deliberately conservative and does not claim detection of encoded,
fragmented, encrypted, transformed, or unknown secrets. Keyword stripping is not used
for prompt injection. Every item retains source, authority, trust, role, reasons, and
correlations; repository governance, repository/test content, local diagnostics, local
deterministic evidence, and provider claims remain distinct.

Content is redacted before SHA-256. `source_digest_sha256` covers the complete authorized
redacted representation; `retained_digest_sha256` covers the exact retained UTF-8 bytes.
These digests are integrity/correlation evidence, never signatures. Deterministic
head/tail UTF-8 truncation includes one fixed marker. Content deduplication is bounded to
authorized candidates and only shares representatives with the same authority and trust;
aliases, reasons, duplicate targets, and avoided bytes remain in the manifest.

The manifest contains metadata and safe digests, not context bodies or absolute roots.
It records injected UTC creation time, stable manifest ID, run/package/task/role and safe
repository/worktree references, policy/limits, bounded included/excluded ledgers,
importance counts, source/retained/excluded/truncated/duplicate/baseline bytes, ratio,
warnings, completeness, and status. Baseline bytes are either a trusted injected
repository measurement or the authorized candidate set. No token or cost field exists.
The in-memory package projects controls and selected content through the existing
`AgentRequest.context` field; provider formatting remains adapter-owned.

P4 orchestration now persists a context intent before selection and a metadata-only
manifest outcome in `CONTEXT_PREPARING`. Schema migration 3 extends the append-only
orchestration attempt-kind constraint with `CONTEXT` by transactionally rebuilding the
table and copying existing P4 rows. Context bodies do not enter SQLite. Current-process
duplicates reuse validated packages; process continuation re-reads authorized inputs and
must reproduce the persisted manifest before any provider can run. Missing external,
identity, size, incomplete, or race evidence blocks; internally invalid selection fails
through the newly authoritative `CONTEXT_PREPARING -> FAILED` edge. Cancellation and stale
revision checks precede selection. The selector cannot transition a run or invoke a
provider, Git, command, or network boundary.

## Alternatives considered

Embeddings/vector search and repository-wide LLM summarization were rejected because they
add nondeterministic relevance, network/provider coupling, cost, and a new injection
surface. Repository-wide keyword crawling was rejected as unbounded and easy to steer.
Provider-owned selection was rejected because it confuses content with authority. Raw
context bodies in SQLite were rejected as duplicate sensitive persistence. A new general
artifact store was rejected because the existing architecture does not yet own one;
approved existing artifacts are read through typed references instead. Filesystem
watchers and indefinite retries were rejected because they do not provide snapshots and
make outcomes timing-dependent.

## Consequences

Python is the only dependency language implemented in version 1. Corresponding tests use
exact filenames below bounded `tests` traversal; custom layouts need explicit evidence.
Governing ADRs must be named explicitly. A same-user process can still replace content
after final verification, and metadata races can evade detection on filesystems with weak
identity/timestamp semantics. Context rematerialization after process restart may block if
the repository changed, by design. Known-pattern redaction is not universal secret
detection. Manifest SHA-256 values authenticate neither source nor actor.

## Status

Accepted and implemented in P5-001; final completion evidence is recorded in the active
work package and `docs/PROJECT_STATE.md`.
