# P6-002: Run, Resume, Status, and Reporting CLI

- **Status:** PLANNED
- **Objective:** Expose the core loop with durable human/machine reports and safe resume.
- **Requirements:** FR-011, FR-012, FR-013, OPS-001, OPS-002, OPS-004, OPS-005.
- **Dependencies:** P4-002, P5-002, P6-001.
- **In scope:** `run`, `resume`, `status`, `report`, `cancel`; progress; run directory layout;
  JSON/Markdown reports; reproduction commands; interrupt/resume E2E tests.
- **Out of scope:** auto commit/push/merge, web dashboard, remote service.
- **Steps:** wire use cases; atomically emit artifacts; render concise progress/final verdict;
  implement cancellation/resume reconciliation; test every final state and report completeness.
- **Security constraints:** reports redact secrets and avoid excessive source; no implied publishing.
- **Acceptance criteria:** interrupted fake run resumes without duplicate side effects; report includes all
  required evidence and accurate labels; new user understands result from final report alone.
- **Verification:** canonical gates plus `uv run pytest tests/integration tests/e2e` and CLI smoke runs.
- **Completion evidence:** Not started.
- **Risks:** synchronization between SQLite and file artifacts during crashes.
- **Recommended model/effort:** GPT-5.6 Sol, high.
- **Next package:** P7-001 — Live Integration and Reliability Hardening.
