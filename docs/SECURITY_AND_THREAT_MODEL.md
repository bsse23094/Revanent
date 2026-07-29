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

Commands use argument arrays, allowlists, bounded output/time, restricted working
directories, filtered environments, cancellation, and redaction. Paths are resolved,
case-normalized on Windows, checked for traversal and link escapes, and compared to
allowed/forbidden rules. Git refuses unexplained dirt, force/reset/push/merge, and
cleans only ownership-recorded worktrees. Provider schemas are versioned and parsed
strictly. Approval is computed locally from evidence, never accepted from prose.
State/event changes are transactional and resume reconciles side effects.

## Residual risks

An explicitly authorized local agent can still exploit allowed tooling or unknown
platform behavior. The MVP is not a security sandbox. Network denial and filesystem
scope enforcement depend on the execution environment as well as Revanent policy.
Live adapters require adversarial review before release.
