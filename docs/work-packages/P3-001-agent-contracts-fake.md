# P3-001: Agent Contracts and Fake Adapter

- **Status:** PLANNED
- **Objective:** Establish provider-neutral requests/responses and deterministic fake outcomes.
- **Requirements:** FR-005, FR-006, NFR-002, NFR-003, NFR-004.
- **Dependencies:** P1-001, P2-001.
- **In scope:** agent port, capability/request/response schemas, status normalization, fake success,
  malformed, timeout, failure, and repair-loop scripts; contract/unit tests.
- **Out of scope:** live OpenCode/Codex and full orchestration.
- **Steps:** freeze v1 contracts; implement strict parsing/artifact references; implement scripted fake;
  test all outcomes, usage labeling, forbidden response fields, and deterministic replay.
- **Security constraints:** response content untrusted; paths normalized; raw output referenced/bounded.
- **Acceptance criteria:** orchestration-facing code needs no provider conditional; fake scenarios
  reproduce exactly; malformed data fails safely; gates pass.
- **Verification:** canonical gates plus `uv run pytest tests/contract tests/unit -k agent`.
- **Completion evidence:** Not started.
- **Risks:** leaking provider quirks into neutral contracts.
- **Recommended model/effort:** GPT-5.6 Terra, high.
- **Next package:** P3-002 — OpenCode and Codex Adapters.
