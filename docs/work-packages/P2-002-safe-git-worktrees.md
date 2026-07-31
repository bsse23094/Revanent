# P2-002: Safe Git Worktrees

- **Status:** COMPLETE - 2026-07-30
- **Objective:** Inspect repositories and manage only Revanent-owned isolated worktrees safely.
- **Requirements:** FR-002, FR-004, NFR-001, NFR-003, NFR-004, SEC-005, SEC-008,
  OPS-002, OPS-003, OPS-007.
- **Dependencies:** P1-002, P2-001 - satisfied.
- **In scope completed:** Git port/local adapter, repository identity and porcelain snapshots,
  protected/owned branch policy, atomic ownership store, create/verify/preserve/remove lifecycle,
  base/ancestry evidence, race refusal, real temporary-repository tests.
- **Out of scope preserved:** provider calls, orchestration, validation/context selection, CLI
  run/resume, commit, merge, push/publication, force/reset/clean/prune, branch deletion, network.
- **Architectural decision:** ADR-0005 records common-repository identity, a separate versioned
  ownership JSON store, partial/stale preservation, dedicated retained branches, and non-force
  cleanup authorization.

## Implemented contract

`revanent.ports.git` defines immutable version-1 `RepositoryIdentity`, `RepositoryStatus`,
`RepositorySnapshot`, `WorktreeSnapshot`, `WorktreeOwnershipRecord`, creation/verification/
cleanup requests and results, `WorktreeId`, lifecycle/operation/error enums, explicit typed
errors, and the `GitRepository` protocol. The port exposes neither raw Git output nor
`CommandResult`. `revanent.git.LocalGitRepository` is the concrete adapter; every Git process
uses the controlled `CommandRunner` and no Git production module imports `subprocess`.

## Repository identity and inspection

Identity records canonical worktree root, per-worktree Git directory, common Git directory,
object format, and sorted root commits reachable from HEAD. `repository_id` hashes the
normalized common directory, object format, and root commits, so linked worktrees match while
moves/replacements/unrelated root histories fail closed. It is local correlation evidence,
not a Git-issued or cryptographic UUID.

Inspection rejects non-repositories, bare/unborn repositories, disallowed UNC forms, malformed,
undecodable, incomplete, or truncated output. It resolves exact HEAD and parses
`status --porcelain=v2 -z --branch` plus `worktree list --porcelain -z`; supported filenames
retain spaces, Unicode, quotes, metacharacters, tabs, and POSIX newlines. It reports staged,
unstaged, untracked, conflicted, ignored, upstream, attached/detached HEAD, linked registry,
and merge/rebase/cherry-pick/revert/bisect/sequencer state. A local `origin/HEAD` is used when
available; no remote/network query occurs.

## Git and protected-branch policy

The internal command surface is limited to `rev-parse`, `rev-list`, `status`, `worktree`
list/add/remove, `symbolic-ref`, `show-ref`, `check-ref-format`, read-only local `config`,
`check-ignore`, and `merge-base`. Inputs are literal ordered arguments with explicit timeouts,
eight-MiB per-stream capture, and expected exit codes. Force/delete/prune flags and unapproved
worktree actions are rejected before launch. There is no reset, clean, push, fetch, pull, merge,
rebase mutation, commit, tag/remote/config mutation, maintenance, or branch deletion code path.

Task branches must be valid option-safe literal refs under `revanent/`. Defaults protect exact
`main`/`master`, `release/*`, `protected/*`, and a locally discoverable default branch; rules are
constructor-configurable. A protected branch/ref may supply the immutable base commit, but task
mutation never uses it directly. Revisions accept only `HEAD`, commit IDs, or full local head/tag
refs and use `--end-of-options` during resolution.

Git commands receive a selected environment, not the raw host environment. System/global config,
prompt/credential interaction, pagers, editors, fsmonitor, attributes-system config, and hooks are
neutralized. Local `filter.*.(clean|smudge|process)` configuration blocks checkout to prevent
repository-configured external filter execution. This conservative rule can reject legitimate
filter-based repositories.

## Ownership and creation invariant

Ownership records live in a caller-provided existing Revanent-owned directory, separate from
SQLite run state. Validated ID-derived filenames prevent traversal. Per-ID exclusive lock files,
bounded reads, restrictive `mkstemp` files, fsync, and atomic replacement serialize local writes.
Records include schema/worktree/run IDs, repository identity, source/worktree paths, branch,
immutable base/created HEAD, UTC time, Revanent version, lifecycle, partial category, and cleanup
evidence. Unknown versions/fields, malformed/oversized/symlinked/mismatched records are rejected.

Creation requires a clean, conflict-free, operation-free source. State/worktree roots inside the
source must already be ignored; Revanent never changes `.gitignore`. It validates/resolves the
base, target containment/parent/link state, owned/non-protected branch, record/path/ref/registry
collisions, and checkout config; rechecks mutation-sensitive state; invokes normal worktree add;
then verifies live common identity, path, branch, HEAD, and Git registration before marking the
record `ACTIVE`. The original worktree is never checked out, stashed, reset, cleaned, restored, or
committed and remains status-equivalent. Two same-ID creations serialize; branch/path races yield
one success at most and otherwise preserve/refuse ambiguous evidence.

An ownership record is written as `CREATING` before Git mutation. A later failure becomes
`PARTIAL` with a sanitized category when that update is possible. No destructive rollback occurs;
the record, branch, directory, and registry entry remain available for human recovery. Stale lock
files also block rather than being guessed stale or automatically removed.

## Cleanup authorization invariant

Cleanup requires an `ACTIVE` record plus matching live common identity, contained canonical path,
unique Git registry entry, owned/non-protected branch, worktree/run identity, and HEAD descended
from the recorded base. The worktree must be unlocked and have no tracked, staged, unstaged,
untracked, conflicted, ignored, merge, rebase, cherry-pick, revert, bisect, or sequencer state.
Cleanup then invokes only normal `git worktree remove -- <path>`, verifies deregistration, retains
the branch, and atomically marks the retained record `REMOVED` with cleanup HEAD/time. A verified
removed record is idempotent. Git refusal never triggers a force retry. Partial, stale, unowned,
missing, replaced, dirty, or ambiguous resources are preserved.

## Tests and completion evidence

The P2-002 focused suite contains 81 tests across contract, unit, and real-Git integration files.
On Windows CPython 3.12.11 with Git 2.54.0.windows.1, 80 pass and the one POSIX-only filename test
is skipped because Win32 cannot create tab/newline components. The Windows junction/reparse-point
escape test runs without symlink privilege. The full suite contains 269 tests: 268 pass and that
same one skips. The pre-edit P2-001 baseline remained 188/188 green.

Coverage proves discovery/bare/linked identity, branch/detached/exact base/upstream, all dirty and
operation classes, special paths, protected/invalid/option-like refs, traversal/sibling/link/
junction targets, UNC default rejection through path policy, spaces, all collisions, concurrency,
creation/post-verification, atomic/schema/tamper/replacement records, partial/stale preservation,
clean/dirty/ignored/locked/race/unowned cleanup, no force/reset/clean/publication path, retained
records/branches, unchanged source, hook/filter controls, and command-boundary architecture.

## Exact verification

- `uv sync --dev` - exit 0; 27 packages resolved, 27 audited.
- `uv run ruff format --check .` - exit 0; 91 files already formatted.
- `uv run ruff check .` - exit 0; all checks passed.
- `uv run mypy src tests` - exit 0; 50 source files, no issues.
- Focused P2-002 suite - exit 0; 80 passed, 1 skipped on Windows.
- `uv run pytest tests/unit tests/contract tests/integration` - exit 0; 268 passed,
  1 skipped on Windows.
- `uv run pytest` - exit 0; 268 passed, 1 skipped on Windows CPython 3.12.11.
- `uv run revanent doctor` - exit 0; Python/platform/uv/Git/Codex available, OpenCode unavailable.
- Security/architecture scans - no Git subprocess bypass, shell enablement, arbitrary public Git
  argument port, worktree force/prune, reset/clean/push/merge/commit execution, branch deletion,
  unowned tree deletion, string-prefix containment, raw environment forwarding, credential,
  unbounded read, or unbounded wait path. The only P2-002 `unlink` removes a validated owned
  `.lock`/`.tmp` file inside the ownership root.
- `git diff --check` - exit 0; line-ending normalization warnings only.

These are the exact final post-documentation results.

## Platform and residual limitations

Current execution evidence is Windows only. The existing CI matrix runs Windows/Linux with Python
3.12/3.13, and POSIX parser/link/filename branches exist, but no remote CI result is claimed here.
UNC is rejected by default; explicitly authorized UNC behavior is not locally verified. The
adapter requires Git features present in the tested Git 2.54 and does not yet expose capability
negotiation for older Git.

The identity is not cryptographic, and a malicious same-user process can rewrite both the state
directory and matching Git metadata. Root identity changes after an unrelated-history merge.
Filesystem validation, atomic records, Git locking, and post-checks cannot eliminate every
validation/use race. Interrupted locks and `PARTIAL` records require manual recovery. The source
must be clean; no explained-dirt exception is implemented. Branches are deliberately retained and
have no automatic cleanup. No CLI/orchestration/run-state integration exists yet.

- **Acceptance criteria:** Met for the library package; user work is preserved, only clean live-
  verified owned worktrees are removable, state is auditable/recoverable, and integration tests pass.
- **Blockers:** None. OpenCode absence is irrelevant to P2-002.
- **Recommended next model/effort:** GPT-5.6 Terra, high.
- **Next package:** P3-001 - Agent Contracts and Fake Adapter.
