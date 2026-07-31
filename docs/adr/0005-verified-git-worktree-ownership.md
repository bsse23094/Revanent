# ADR-0005: Verified Git worktree ownership and conservative cleanup

## Context

Revanent needs isolated task workspaces without granting a local automation layer the
ability to overwrite unexplained repository state. A directory name or JSON marker is
not enough to authorize deletion: linked worktrees belong to Git's common repository,
paths may be replaced, records may be stale, and a clean branch can still contain user
commits that must remain reachable.

## Decision

Expose repository inspection and worktree lifecycle through a provider-independent
`GitRepository` port. The concrete local adapter invokes only a fixed inspection,
worktree-add, and normal worktree-remove surface through the P2-001 `CommandRunner`.
It parses porcelain version 2 and NUL-delimited worktree data; it never accepts an
arbitrary Git argument list from callers.

Repository identity version 1 records the canonical inspected worktree root, canonical
per-worktree Git directory, canonical common Git directory, object format, and root
commits reachable from the inspected HEAD. `repository_id` hashes the normalized common
Git directory, object format, and sorted root commits so linked worktrees share the same
common identity while moves, replacements, and unrelated-history changes fail closed.
The immutable base commit is recorded separately for each owned worktree.

Ownership records are strict, versioned JSON in a dedicated caller-provided
Revanent-owned state directory, not in the SQLite run schema. Record filenames derive
only from validated `WorktreeId` values. Per-ID create-exclusive lock files serialize
local lifecycle mutations; bounded records are written to an owned temporary file,
synced, and atomically replaced. Lifecycle states are `CREATING`, `ACTIVE`, `PARTIAL`,
and `REMOVED`. Records remain after cleanup and `REMOVED` carries cleanup HEAD and UTC
timestamp evidence.

Creation requires a clean, conflict-free, operation-free source snapshot, an exact
resolved base commit, a dedicated `revanent/` branch that is not protected, contained
nonexistent target, no record/ref/worktree collision, and a second mutation-sensitive
inspection. State/worktree roots inside the source must already be ignored. Repository
hooks and fsmonitor are neutralized, system/global Git config is disabled, and any
local clean/smudge/process filter configuration blocks checkout. Git registration,
common identity, branch, path, and HEAD are live-verified before the record becomes
`ACTIVE`.

Cleanup requires an `ACTIVE` record plus matching live identity, target, registry,
branch, and base ancestry. The owned worktree must be unlocked, operation-free, free of
tracked/staged/untracked/conflicted changes, and contain no ignored paths. Cleanup uses
only normal `git worktree remove -- <path>`, verifies deregistration, retains the branch,
then marks the record `REMOVED`. There is no force fallback, branch deletion, reset,
clean, prune, push, merge, commit, or direct filesystem removal of a worktree.

Failures after the initial ownership write preserve `PARTIAL` evidence when possible.
Partial records, stale locks, missing worktrees, mismatches, and ambiguous cleanup
remain blocked for human recovery. Revanent does not attempt destructive rollback.

## Alternatives considered

Directory-prefix ownership was rejected because an unowned or replaced directory can
use the same name. Git registry data alone was rejected because it does not establish
Revanent intent or a stable run/worktree identifier. Storing ownership only in SQLite
would couple Git recovery to run-schema availability and make partial creation before a
run transition harder to explain. Detached worktrees would avoid branch collisions but
make user commits easier to orphan. Automatic force removal, pruning, hard reset, clean,
stashing, and branch deletion were rejected because they can destroy unexplained work.

## Consequences

The policy is intentionally conservative. Dirty source worktrees and repositories with
configured checkout filters are refused even when a narrower operation might sometimes
be safe. Branches survive cleanup, preserving commits at the cost of later manual branch
management. Interrupted lock files and partial records require inspection rather than
automatic reclamation.

The identity is not a cryptographic repository UUID, and records are not protected
against a malicious same-user process that can rewrite the state directory and matching
Git metadata. Path resolution and post-verification cannot eliminate a concurrent
filesystem replacement between check and Git use. Root-commit identity changes after an
unrelated-history merge. UNC roots are rejected by default and require both Git-adapter
and path-policy authorization; no local UNC guarantee is claimed.

## Status

Accepted and implemented - 2026-07-30.
