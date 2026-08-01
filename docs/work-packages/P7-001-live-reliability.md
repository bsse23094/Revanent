# P7-001: Live Integration and Reliability Hardening

- **Status:** IN PROGRESS - C1 partial; live provider certification blockers remain.
- **Objective:** Evidence a real local-builder/Codex path and harden failure/security behavior.
- **Requirements:** FR-006, FR-014, SEC-001 through SEC-008, OPS-002, OPS-004, OPS-008.
- **Dependencies:** P6-002 and installed/configured providers.
- **In scope:** opt-in live tests, capability matrix, crash/timeout/cancel tests, adversarial security
  review, cross-platform CI evidence, performance profiling, operational docs.
- **Out of scope:** paid calls by default, unsupported providers, hosted deployment.
- **Steps:** install/configure external prerequisites with human authorization; run fixture live path;
  audit boundaries; repair findings; repeat validation/review; document exact versions/evidence.
- **Security constraints:** bounded fixture repositories; minimal credentials/context; no remote Git writes.
- **Acceptance criteria:** one evidenced OpenCode-to-Codex run; failure/recovery verified; Windows/Linux
  gates pass; no unresolved critical/high security finding.
- **Verification:** canonical gates, opt-in live marker commands documented at execution, security checklist.
- **Completion evidence:** C1 added default-off role-scoped certification and exercised bounded
  disposable live scenarios. OpenCode remains unavailable; Codex capability probes pass, but the
  reviewer JSONL and repair response envelope are not yet certified. See the C1 record below.
- **Risks:** external CLI drift, model nondeterminism, credentials and network policy.

## P7-001-C1: Opt-In Live Provider Certification

- **Status:** PARTIAL - 2026-08-01. P7-001 and Phase 7 remain in progress.
- **Opt-in:** Default pytest excludes `live`. Selecting live tests still requires `--live-certify`,
  the exact acknowledgement, an explicit per-role model, and finite timeout/token/cost ceilings.
  Production runtime additionally requires `allow_network`, `allow_live_opencode_builder`, and
  `allow_live_codex_reviewer`; repair retains its separate write authorization.
- **Harness:** Each mutating scenario creates a new temporary Git repository and linked owned
  worktree outside the Revanent checkout. It permits one provider invocation, uses bounded scope and
  output, and records metadata-only schema-v1 certification evidence without prompts or raw output.
- **Verified live results:** OpenCode detection returned `UNAVAILABLE` and performed zero provider
  invocations. Codex capability surfaces were available. One read-only reviewer call returned JSONL
  but was rejected as `contradictory_terminal_events`; offline analysis established that Codex emits
  bounded interim agent messages, so the parser now selects the final completed message. One
  separately authorized repair call reached Codex but its terminal envelope failed strict validation
  as `invalid_response_schema`; no successful repair was claimed.
- **Live safety:** The Revanent checkout was never targeted. The disposable source checkout stayed
  clean and unchanged. No push, merge, post-fixture commit, publication, reset, clean, stash, tag,
  credential probe, provider installation, SDK, direct HTTP, or pricing lookup occurred.
- **Findings repaired:** Controlled provider stdin had accidentally remained disabled in production
  composition. It is now enabled only after all live authorization flags pass. Live prompts now
  include the exact trusted adapter identity and response JSON schema.
- **Blockers:** OpenCode executable absent. Codex strict response-envelope compatibility remains
  uncertified. No complete live Run, live telemetry/report, or ApprovalGate certification occurred.
- **Verification:** Ruff format/lint and mypy pass. Focused offline C1 paths pass 44 tests. Both
  categorized and full offline suites pass 684 tests with two expected Windows skips; the full run
  deselects three live tests. Doctor reports OpenCode unavailable and Codex CLI
  `0.146.0-alpha.9.2` available. `git diff --check` passes with expected line-ending notices.
- **Live calls:** OpenCode provider calls: zero. Codex reviewer: one costed invocation, rejected.
  Codex repair: one costed invocation, rejected. Earlier policy-rejected attempts launched no model.
- **Next internal pass:** P7-001-C2 - Cross-Platform Hardening, Final Certification, and Phase 7
  Completion.
- **Recommended model/effort:** GPT-5.6 Sol, xhigh; multi-agent only for independent security/test audits.
- **Next package:** P8-001 — Evaluation Benchmark.
