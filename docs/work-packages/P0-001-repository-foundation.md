# P0-001: Repository Foundation

- **Status:** COMPLETE
- **Objective:** Turn the empty directory into an authoritative, importable, tested foundation.
- **Requirements:** NFR-001, NFR-005, NFR-006, OPS-004, OPS-006.
- **Dependencies:** None.
- **In scope:** root metadata, authoritative docs/ADRs/roadmap, Python CLI/version/doctor,
  tests, lockfile, Git initialization, and Windows/Linux CI.
- **Out of scope:** config/domain schemas, persistence, worktrees, providers, orchestration.
- **Steps:** record discovery; write docs/decisions; scaffold package; add read-only doctor;
  add unit/contract tests and CI; lock; initialize Git; run gates; record evidence.
- **Security constraints:** no environment dump; fixed-argument bounded probes; no provider calls;
  no destructive or remote Git action; no invented live evidence.
- **Acceptance criteria:** all authoritative docs exist; package imports; CLI help/version/doctor
  work; uv lock is consistent; format, lint, strict types, and tests pass; state names P1-001.
- **Verification:** `uv lock --check`; `uv run ruff format --check .`; `uv run ruff check .`;
  `uv run mypy src tests`; `uv run pytest`; `uv run revanent --help`;
  `uv run revanent doctor`; `git status --short`.
- **Completion evidence:** Completed 2026-07-29 on Windows x64 with uv-selected CPython
  3.12.11. `uv lock --check` resolved 27 packages; Ruff format reported 47 files already
  formatted; Ruff lint passed; strict mypy checked 9 source files with no issues; pytest
  passed 8 tests; CLI help and default doctor exited 0. Doctor detected Git 2.54.0,
  uv 0.7.13, Codex CLI 0.146.0-alpha.3.1, and accurately reported OpenCode unavailable.
  Strict doctor exited 1 for that provider gap. Both YAML files parsed successfully,
  the credential-shaped-value scan found none, and Git reports only the explained
  initial untracked foundation on `main`; no commit, remote, push, or merge was made.
- **Risks:** platform-specific tool discovery; version drift in initial lock.
- **Recommended model/effort:** GPT-5.6 Sol, high, for initial architecture and safety boundaries.
- **Next package:** P1-001 — Domain, Configuration, and State Machine.
