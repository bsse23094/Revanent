# P4-001: Validation and Structured Review Gates

- **Status:** COMPLETE 2026-07-30
- **Objective:** Make deterministic validation and structured review necessary for approval.
- **Requirements:** FR-007, FR-008, FR-015, NFR-002/003/004/006/007, SEC-001/002/003/004/006/007/008, OPS-004/007.
- **Dependencies:** P2-001, P3-001.
- **In scope:** validation plan/runner service, review schema v1 reuse, finding IDs/severity,
  parse errors, local approval-gate computation, JSON evidence, unit/contract/integration tests.
- **Out of scope:** repair selection, orchestration, persistence, transitions, context, budgets,
  CLI workflow, live providers, network, and Git mutation.
- **Security constraints:** model cannot self-authorize; validation evidence is immutable,
  bounded, correlated, and replayed; local facts never come from provider payload.
- **Acceptance criteria:** no failed/missing/incomplete validation can approve; only a
  correlated canonical REVIEWER payload is considered; every negative gate fails explicitly.
- **Completion evidence:** Immutable validation schema v1, ordered `CommandRunner`-only
  execution, typed normalization/aggregation, strict reviewer correlation, pure local
  gate decisions, and local-only `ApprovalGate` construction are implemented. Focused
  unit/contract/architecture/integration coverage passes with fake agents and controlled
  fake commands; the canonical suite and security scans pass. No live provider, network,
  Git mutation, orchestration, repair selection, persistence, or transition was introduced.
- **Decisions:** ADR-0008. Existing `ReviewResult`, `ReviewFinding`, and `ApprovalGate`
  remain authoritative. Stable finding IDs are a deterministic local projection because
  the accepted finding schema has no ID. Separate local evidence owns facts a provider
  cannot establish. Advisory failures require explicit policy; security-critical checks
  cannot be advisory; cancellation always stops later launch.
- **Status mapping:** expected successful exit -> `PASSED`; unexpected exit -> `FAILED`;
  timeout -> `TIMED_OUT`; cancellation -> `CANCELLED`; executable unavailable ->
  `UNAVAILABLE`; policy/launch refusal -> `BLOCKED`; malformed/internal/artifact evidence
  -> `INVALID`; explicit non-execution -> `NOT_RUN`.
- **Approval rule:** only complete, replay-valid validation plus a correlated completed
  REVIEWER response, canonical approved review, no high/critical findings, verified
  read-only identity, and passing local scope/generated/lock/artifact/cleanliness/
  side-effect facts can create `ApprovalGate`. Prose is ignored.
- **Risks and limitations:** The local evidence producer, durable storage, workflow
  transition, orchestration, and repair selection remain P4-002. Command artifact
  references have no content digest and retain a filesystem inspection/use race. Unknown
  or transformed secrets cannot be inferred by redaction.
- **Verification:** `uv sync --dev`; canonical format/lint/type/test/doctor gates;
  focused P4-001 suite; unit/contract/integration regression; architecture/security scans;
  `git diff --check`. Exact counts are recorded in `docs/PROJECT_STATE.md`.
- **Recommended model/effort:** GPT-5.6 Terra, high for P4-002 implementation; separate
  GPT-5.6 Sol high review for approval bypass and evidence integrity.
- **Next package:** P4-002 — Bounded Orchestration and Explicit Repair Policy.
