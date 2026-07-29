# Testing Strategy

Unit tests cover configuration, state transitions, repair/path policy, command
construction, schema parsing, usage accounting, context selection, reports, and
redaction. Contract tests freeze external and persisted schema versions. Integration
tests use fake executables and temporary repositories/worktrees. End-to-end fixtures
cover approval, validation failure, both repair paths, limits, interruption/resume,
forbidden paths, dirty state, and missing dependencies.

The default suite makes no paid or live provider calls. Live OpenCode/Codex tests are
explicitly opted in and marked; simulated results never count as live evidence.

Canonical local and CI gates:

```text
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest
```

CI runs the gates on Windows and Linux with Python 3.12 and 3.13. Security-sensitive
changes require negative tests at the relevant trust boundary. Tests assert actual
behavior and avoid replacing the behavior under test with ceremonial mocks.
