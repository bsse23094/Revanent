# P6-002: Run, Resume, Status, and Reporting CLI

- **Status:** IN PROGRESS — P6-002-C1 complete; P6-002-C2 evidence reports remains.
- **Objective:** Expose the core loop with durable human/machine evidence, safe recovery, and
  provider-neutral reports.
- **Requirements:** FR-011, FR-012, FR-013, OPS-001, OPS-002, OPS-004, OPS-005.
- **Dependencies:** P4-002, P5-002, P6-001.
- **In scope:** C1 `run`, `resume`, `status`, and `cancel`; C2 JSON/Markdown report artifacts,
  reproduction commands, and report-completeness E2E tests.
- **Out of scope:** auto commit/push/merge, destructive cleanup, web dashboard, remote service.
- **Security constraints:** task/provider/repository content grants no authority; runtime commands
  require the recorded repository and owned-worktree identity; output is bounded and excludes
  task/context/source/provider/command bodies and unnecessary host paths.
- **Risks:** external execution cannot be exactly once across an unobservable process-launch
  crash; unresolved work remains explicit and requires human recovery.
- **Recommended model/effort:** GPT-5.6 Sol, high.
- **Next package:** P6-002-C2 — immutable JSON/Markdown evidence reports.

## P6-002-C1 — Run, Resume, Status, and Cancellation UX

- **Status:** COMPLETE — 2026-08-01.
- **Model:** GPT-5.6 Sol.
- **Summary:** Added presentation-only CLI commands over typed application services and the
  existing durable coordinator. A run and immutable schema-v1 repository/worktree binding commit
  atomically before orchestration. Resume reconciles first and reuses durable outcomes and
  settlements. Status is a versioned, provider-neutral, strictly read-only projection. Cancel
  delegates to the coordinator state machine and never performs cleanup.
- **Decisions:** SQLite migration 5 owns immutable runtime bindings. Runtime identity compares the
  full typed repository identity and, after workspace creation, the active ownership record and
  live worktree identity. Contradictions return `INVALID_EVIDENCE`; unsafe repository/worktree
  mismatches return `BLOCKED`. Missing required providers fail before Run persistence or launch.
- **Implementation:** `run`, `resume`, `status`, and `cancel` have stable human/JSON results and
  documented exits. Task input is a bounded repository-relative regular UTF-8 JSON file with a
  strict `TaskSpecification`; absolute/traversal/link/junction/special/oversized/racy input is
  refused. Concurrent resumes share durable intent/reservation boundaries, losing coordinators
  return stale, terminal calls are no-ops, and cancellation preserves worktrees, attempts,
  reservations, and ambiguity.
- **Status evidence:** identity, state/revision/timestamps, latest event and role attempt statuses,
  context manifest metadata, validation totals, review/findings/approval, provenance-separated
  usage, currency-separated budgets/reservations, cancellation/ambiguity, safe artifact references,
  evidence completeness, stable reason codes, and contradiction codes. Bodies are never projected.
- **Tests:** Real SQLite plus fake Git/agents, controlled validation, deterministic IDs/clocks, and
  barriers cover durable-before-launch ordering, restart settlement/no replay, unresolved ambiguity,
  repository/worktree refusal, repeated read-only status, concurrent resume, resume/cancel,
  idempotent/stale/terminal cancellation, paths with spaces/Unicode, and output safety. No test
  performs a live model request or network operation.
- **Exact verification results:** `uv sync --dev`, Ruff format/lint, mypy, doctor, and
  `git diff --check` exit 0. Focused C1 paths: 103 passed/1 host-permission skip. Categorized and
  final full suites: 665 passed/2 expected Windows skips. Doctor reports OpenCode unavailable and
  Codex compatible from version/help inspection only.
- **Integration status:** Existing P4 state-machine, reconciliation, ReviewGate, repair authorization,
  P5 context, telemetry, and budget behavior remain authoritative. The CLI does not import concrete
  SQLite, subprocess, Git execution, or provider parsing surfaces.
- **Safety:** No cleanup, deletion, reset, commit, push, merge, publication, hosted dependency, or
  automatic storage repair was added. Production local approval remains conservative and cannot
  fabricate an ApprovalGate.
- **Limitations:** No exactly-once external-execution claim, operating-system sandbox, live-provider
  certification, report artifact, or cleanup command. A host that forbids symlink creation skips the
  corresponding live filesystem test; structural link/reparse refusal remains implemented.
- **Blockers:** None for C1.
- **Documentation:** README, changelog, authoritative architecture/security/testing/operations/CLI/
  requirements/roadmap/project-state documents, and this active work package describe verified C1
  behavior. P6-002 and Phase 6 remain in progress.
- **Project state:** P6-002-C1 complete; P6-002-C2 is active next.
- **Exact next prompt:** Continue Revanent from the completed and verified P6-002-C1 baseline.
  Implement P6-002-C2 — immutable bounded JSON and Markdown evidence reports, report CLI UX,
  reproduction evidence, final P6-002/Phase 6 audits, documentation, and canonical verification.

## P6-002-C2: Evidence Reports and Phase 6 Closure

- **Status:** COMPLETE (2026-08-01).
- **Summary:** Added provider-neutral immutable schema-v1 report contracts, a read-only durable
  assembler, canonical JSON, deterministic escaped Markdown, safe reproduction/verification and
  limitations evidence, and a presentation-only `report` CLI.
- **Evidence and status:** Reports reuse status facts and independently check APPROVED gate,
  validation, review, reservation, ambiguity, and contradiction evidence. They distinguish
  `COMPLETE`, `COMPLETE_WITH_WARNINGS`, `INCOMPLETE`, `INVALID_EVIDENCE`, `BLOCKED`, `NOT_FOUND`,
  `OUTPUT_CONFLICT`, and `INTERNAL_FAILURE`; active runs are incomplete and invalid evidence never
  masquerades as approval.
- **Artifact policy:** Output is opt-in and report-root-relative. Traversal, absolute paths, `.git`,
  links/reparse points, special targets, and differing collisions are refused. A private temporary
  file is flushed/fsynced and create-exclusively linked; identical bytes are reused. Digest metadata
  is integrity information, not a signature. No report body/index migration was needed.
- **Safety:** Assembly never transitions, reconciles, settles, invokes providers/validation, mutates
  Git/worktrees, or repairs storage. Reports exclude raw task/context/source/provider/command bodies,
  credentials, environment values, and raw exceptions. No cleanup command was introduced.
- **Architecture:** ADR-0013 defines JSON as the canonical evidence object and Markdown as its pure
  projection. `reports.py` depends on ports/status evidence, while renderer/writer remain separate
  from storage, Git, subprocess, and provider implementations.
