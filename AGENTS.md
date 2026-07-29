# Revanent contributor instructions

Revanent is a local-first orchestration layer that coordinates a local builder,
deterministic validation, and a Codex review/repair gate. The MVP is a Python
3.12+ CLI and local library; it does not push, merge, or require hosted services.

## Canonical commands

```text
uv sync --dev
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest
uv run revanent doctor
```

## Boundaries and standards

- Domain and orchestration code must not import provider implementations.
- External commands, Git, persistence, providers, and reporting live behind
  explicit interfaces. State transitions and subprocess execution are centralized.
- Use typed models at boundaries, argument-list subprocesses, pathlib paths,
  UTC timestamps, stable IDs, and explicit errors. Support Windows first while
  retaining POSIX compatibility.
- Add tests for behavior and defects. Do not weaken validation to make it pass.
- Never log secrets, pass the full host environment to agents, or use shell
  interpolation for repository-controlled values.
- Never force, reset destructively, delete untracked files, push, merge, publish,
  or modify protected branches without explicit human approval.
- Do not couple to undocumented OpenCode or Codex flags. Detect capabilities.
- Update the applicable files under `docs/`, the active work package, and
  `docs/PROJECT_STATE.md` with each completed or blocked package.

Architecture and policy details are authoritative in `docs/ARCHITECTURE.md`,
`docs/SECURITY_AND_THREAT_MODEL.md`, and `docs/TESTING_STRATEGY.md`.

## Required handoff

Final responses must use the section order in the active work package and include:
package/status/model, summary, decisions, created/modified files, implementation,
tests, exact verification results, integration status, safety, limitations,
blockers, documentation, project state, and the exact next prompt.
