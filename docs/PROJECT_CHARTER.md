# Project Charter

## Problem statement

Coding agents can implement quickly but often receive excessive context, mutate an
unsafe workspace, or report success without deterministic evidence. Using a premium
remote model for every mechanical step is also unnecessarily expensive.

## Intended users

Revanent serves individual developers and small engineering teams who want a local
builder to perform routine repository work while retaining a strong, auditable
Codex quality gate.

## Goals

- Run a bounded local-builder, validation, review, and repair loop.
- Isolate agent edits in Git worktrees and preserve unexplained user changes.
- Make every significant decision resumable and auditable from local state.
- Minimize model context without omitting evidence required for safe approval.
- Remain provider-neutral at domain boundaries and Windows-first in behavior.

## Non-goals

The MVP is not a hosted multi-user service, dashboard, IDE, training system,
billing platform, distributed queue, autonomous merger, or universal provider
integration. It does not allow arbitrary shell execution without policy checks.

## Success criteria

The MVP succeeds when fixture repositories demonstrate successful approval,
validation and review failure, both repair paths, enforced limits, interruption and
resume, forbidden-path detection, dirty-tree preservation, and clear dependency
blockers without paid calls. A later live gate must evidence one OpenCode-to-Codex
run and no unresolved critical/high security findings.

## Operating principles

Safety and evidence outrank speed. Local-first does not mean lower quality. Defaults
are bounded and reversible. Provider output is untrusted. Measured and estimated
usage are labeled separately. Humans authorize destructive and publishing actions.

## MVP boundary

The MVP is a Python CLI and library using local SQLite/JSON artifacts, native Git
worktrees, OpenCode as the initial builder, Codex as reviewer/repairer, deterministic
validation, and Markdown/JSON reports. See `REQUIREMENTS.md` for the normative scope.
