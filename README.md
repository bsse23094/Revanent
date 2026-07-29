# Revanent

Revanent is a local-first, model-agnostic software-engineering orchestrator. It
delegates bounded implementation to a local coding agent, runs deterministic
validation, and uses Codex as a structured review and repair gate.

The project is in Phase 0. The current executable surface is a foundation CLI
and an environment doctor; the orchestration loop is intentionally not yet
advertised as complete.

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

Revanent does not push, merge, or delete user work by default. Provider execution,
Git mutations, and unrestricted command execution are outside the current Phase 0
implementation.

## License

All rights reserved until the project owner selects an open-source license.
