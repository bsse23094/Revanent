# P7-001: Live Integration and Reliability Hardening

- **Status:** PLANNED
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
- **Completion evidence:** Not started; currently blocked for live OpenCode only because CLI is absent.
- **Risks:** external CLI drift, model nondeterminism, credentials and network policy.
- **Recommended model/effort:** GPT-5.6 Sol, xhigh; multi-agent only for independent security/test audits.
- **Next package:** P8-001 — Evaluation Benchmark.
