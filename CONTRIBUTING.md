# Contributing

Read `AGENTS.md` and `docs/PROJECT_STATE.md` before making changes. Work from a
bounded work package, keep changes within its declared scope, and update its
completion evidence.

Run before handoff:

```text
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest
```

Commits should be focused and must not contain credentials, generated run state,
provider transcripts, or unrelated formatting changes. Do not push or merge on
another person's behalf without explicit authorization.
