# Revanent

Revanent is a local-first, model-agnostic software-engineering orchestrator. It
delegates bounded implementation to a local coding agent, runs deterministic
validation, and uses Codex as a structured review and repair gate.

The Phase 1 through Phase 4 libraries are complete:
versioned configuration/domain schemas, the central state machine, durable SQLite
run/event primitives, bounded local execution, deterministic Git inspection, and
ownership-verified isolated worktrees, deterministic validation/review gates, and the
finite fake-first durable orchestration/repair service are implemented. User-facing
run/resume commands remain planned. The executable surface remains the foundation CLI
and doctor.

## Development

Requirements: Git, [uv](https://docs.astral.sh/uv/), and Python 3.12 or newer.

```text
uv sync --dev
uv run revanent --help
uv run revanent doctor
uv run pytest
```

Start with [the project charter](docs/PROJECT_CHARTER.md), [architecture](docs/ARCHITECTURE.md),
and [current project state](docs/PROJECT_STATE.md).

## Safety posture

Revanent does not push, merge, or delete user work by default. Its command library
requires explicit executable/path/environment/resource policy, never accepts a shell
command string, and redacts bounded public output and overflow artifacts. Its Git
library refuses dirty/ambiguous ownership, creates only dedicated non-protected branches,
and removes only clean live-verified owned worktrees without force or branch deletion.
This is not an operating-system sandbox. No user-facing command invokes orchestration or
providers by default; the orchestration library is fake-verified and makes no exactly-once
external execution claim.

## License

All rights reserved until the project owner selects an open-source license.
