# Project State

Last updated: 2026-07-29

## Current milestone

Phase 0 — Discovery and Project Foundation exit gate achieved. Phase 1 is next.

## Completed work packages

- P0-001 — Repository Foundation (COMPLETE 2026-07-29).

## Active work package

None. P1-001 is the next planned package.

## Verified commands

- `uv lock --check` — success, 27 packages resolved.
- `uv run ruff format --check .` — success, 47 files already formatted.
- `uv run ruff check .` — success, all checks passed.
- `uv run mypy src tests` — success, 9 source files, no issues.
- `uv run pytest` — success, 8 tests passed on CPython 3.12.11.
- `uv run revanent --help` and `uv run python -m revanent --help` — success.
- `uv run revanent doctor` — success; required runtime available, Codex available,
  OpenCode accurately unavailable. `doctor --strict` returned expected exit code 1.
- YAML parse smoke check — success for example config and CI workflow.
- Credential-shaped-value scan — success, no matches.
- `git diff --check` — success; Git state contains only explained initial untracked files.

## Known limitations

Only package/version/help and a read-only environment doctor exist. There is no
configuration model, domain state machine, persistence, safe general command runner,
Git worktree manager, agent adapter, orchestration loop, or run/resume/report surface.
OpenCode is not installed in the discovery environment. Live integration is untested.

## Blockers

None for Phase 0. OpenCode absence blocks future live builder tests, not current work.

## Architectural decisions

- ADR-0001: Python 3.12+, uv, typed local CLI and adapter boundaries.
- ADR-0002: SQLite current state/events plus versioned file artifacts.

## Next recommended work package

P1-001 — Domain, Configuration, and State Machine. Use Codex GPT-5.6 Sol at high
reasoning because schema boundaries, approval invariants, configuration security, and
transition rules will constrain every later package. A local model may implement
mechanical typed models/tests from the frozen requirements; Codex should own the
invariants and final review.

## Exact next-session bootstrap instruction

Read `AGENTS.md`, `docs/PROJECT_STATE.md`, `docs/ARCHITECTURE.md`,
`docs/REQUIREMENTS.md`, `docs/WORKFLOW_STATE_MACHINE.md`, and
`docs/work-packages/P1-001-domain-config-state-machine.md`. Inspect the repository and
Git status, reconcile existing work, then execute P1-001 only. Implement versioned
Pydantic configuration and core domain schemas plus a centralized, explicitly tested
run-state transition function. Do not implement SQLite, provider adapters, Git
worktrees, or the orchestration loop. Run every package verification command, record
actual evidence in the package and `docs/PROJECT_STATE.md`, and return the required
handoff format.
