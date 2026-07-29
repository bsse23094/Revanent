# Requirements

Status terms are **MUST**, **SHOULD**, and **MAY** in their ordinary normative sense.

## Functional

- **FR-001** Accept a bounded task with allowed/forbidden scope and stable identity.
- **FR-002** Inspect a target Git repository without overwriting unexplained changes.
- **FR-003** Build a deterministic, reason-annotated, task-specific context package.
- **FR-004** Create/select an isolated, run-specific Git worktree when possible.
- **FR-005** Invoke builders and reviewers through provider-neutral typed contracts.
- **FR-006** Support fake, OpenCode builder, and Codex reviewer/repair adapters.
- **FR-007** Run a declared validation plan after every code change.
- **FR-008** Parse a versioned review result with APPROVED, CHANGES_REQUIRED, or BLOCKED.
- **FR-009** Select and record a bounded local-builder or Codex repair strategy.
- **FR-010** Enforce attempt, duration, token/cost, scope, and cancellation limits.
- **FR-011** Persist run state and significant append-only events for safe resume.
- **FR-012** Generate JSON and Markdown evidence reports and artifact references.
- **FR-013** Provide init, doctor, run, resume, status, and report CLI workflows.
- **FR-014** Detect installed provider versions/capabilities without assuming flags.
- **FR-015** Reject approval when validation, parsing, scope, or evidence gates fail.

## Non-functional

- **NFR-001** Support Python 3.12+ on current Windows and maintained Linux runners.
- **NFR-002** Keep domain/orchestration independent of provider and storage adapters.
- **NFR-003** Be deterministic under fake providers and fixture repositories.
- **NFR-004** Fail explicitly on invalid states, schemas, or configuration.
- **NFR-005** Remain locally operable without a hosted Revanent service.
- **NFR-006** Use typed interfaces and strict static analysis for project code.
- **NFR-007** Bound retained command output and reference full artifacts when retained.
- **NFR-008** Distinguish measured, reported, and estimated usage/cost values.

## Security

- **SEC-001** Execute external commands as argument lists without shell interpolation by default.
- **SEC-002** Restrict working directories, executable policy, timeout, output, and cancellation.
- **SEC-003** Filter inherited environment variables and redact secret-like values.
- **SEC-004** Enforce normalized allowed and forbidden path boundaries.
- **SEC-005** Never force, destructively reset, delete untracked work, push, or merge by default.
- **SEC-006** Treat repositories, provider output, command output, and model content as untrusted.
- **SEC-007** Do not persist credentials or excessive duplicate source content.
- **SEC-008** Require tests for security-sensitive command, Git, persistence, and recovery changes.

## Operational

- **OPS-001** Persist enough state to explain and reproduce each completed or blocked run.
- **OPS-002** Make resume idempotent and verify repository/worktree assumptions first.
- **OPS-003** Preserve failed worktrees when configured and clean only owned worktrees.
- **OPS-004** Mark checks as passed, failed, skipped, simulated, or unavailable accurately.
- **OPS-005** Emit concise progress and durable state-transition history.
- **OPS-006** Validate configuration before invoking an agent.
- **OPS-007** Keep schema and artifact format versions explicit and migratable.
- **OPS-008** Provide opt-in, clearly marked live-provider tests.

Each work package names the requirements it advances; complete traceability is
maintained in `ROADMAP.md` and package completion evidence.
