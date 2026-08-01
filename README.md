# Revanent

Revanent is a local-first, model-agnostic software-engineering orchestrator. It
delegates bounded implementation to a local coding agent, runs deterministic
validation, and uses Codex as a structured review and repair gate.

The Phase 1 through Phase 5 foundation libraries and the Phase 6 setup UX are complete:
versioned configuration/domain schemas, the central state machine, durable SQLite
run/event primitives, bounded local execution, deterministic Git inspection, and
ownership-verified isolated worktrees, deterministic validation/review gates, and the
finite fake-first durable orchestration/repair service. Deterministic context packages select
bounded, provenance-labelled source/evidence with metadata-only manifests. Durable telemetry
keeps local bytes, measured duration, provider-reported tokens, estimates, unavailable values,
and unresolved reservations distinct while atomically enforcing supported local budgets. Safe
repository initialization, root-bound configuration validation, read-only doctor diagnostics,
and provider capability detection are implemented. P6-002-C1 exposes bounded `run`, `resume`,
read-only `status`, `cancel`, and `report` workflows over the durable coordinator. Each operation verifies
the Run's immutable repository/worktree binding; status projects bounded evidence without writes,
and concurrent recovery preserves the at-most-once initiation boundary. `report` derives a bounded,
read-only canonical JSON evidence object and deterministic Markdown projection; explicit output uses
an atomic no-clobber report-root writer. Phase 6 is complete; live-provider certification remains P7.

P7 live certification is explicitly opt-in and excluded from ordinary pytest. It requires
role-specific model authorization and finite ceilings, runs only in a disposable repository/owned
worktree, and adds no credential, HTTP, pricing, or publication surface. C1 remains partial because
OpenCode is unavailable and current Codex responses have not passed strict envelope certification.

## Development

Requirements: Git, [uv](https://docs.astral.sh/uv/), and Python 3.12 or newer.

```text
uv sync --dev
uv run revanent --help
uv run revanent doctor
uv run revanent init --repository PATH
uv run revanent config validate --repository PATH
uv run revanent agents detect --repository PATH
uv run revanent run --repository PATH --task-file task.json
uv run revanent resume RUN_ID --repository PATH
uv run revanent status RUN_ID --repository PATH --json
uv run revanent cancel RUN_ID --repository PATH
uv run revanent report RUN_ID --repository PATH --format markdown
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
This is not an operating-system sandbox. `run` and `resume` invoke only explicitly configured,
capability-validated providers; missing required capabilities fail before Run persistence or
launch. Automated verification is fake/local-only, production approval remains conservative,
and Revanent makes no exactly-once external-execution claim.

## License

All rights reserved until the project owner selects an open-source license.
