# P3-001: Agent Contracts and Deterministic Fake Adapter

- **Status:** COMPLETE - 2026-07-30
- **Objective:** Establish provider-neutral requests/responses and deterministic fake outcomes.
- **Requirements:** FR-005; fake portion of FR-006; NFR-002/003/004; agent-boundary
  portions of SEC-006/007 and OPS-004/007.
- **Dependencies:** P1-001, P2-001.
- **Implemented scope:** agent port; capability/request/response/status/failure schemas;
  strict parsing, correlation, semantic normalization, reported usage, diagnostics,
  bounded artifact references; finite scripted fake success/failure/blocker/unavailable/
  timeout/cancellation/malformed/replay behavior; contract/unit/architecture tests.
- **Excluded scope:** live OpenCode/Codex, provider CLI flags beyond existing doctor,
  orchestration, validation, approval gates, repair selection, context selection, budget
  enforcement, run/resume/status CLI, network/paid calls, and Git/worktree expansion.
- **Security constraints:** provider content remains untrusted; parsing is byte/depth/item
  bounded and strict; raw bytes never enter public errors; known secrets are redacted;
  raw output is referenced only when bounded/redacted; reviewers are read-only; adapters
  cannot mutate `Run` or create `ApprovalGate`.

## Completion evidence

- `revanent.ports.agents` owns immutable strict schema-version-1 role, capability,
  request, response, failure, diagnostic, reported-usage, artifact, and port contracts.
- `AgentInvocationId` and `AgentAttemptId` use canonical validated spelling. Response
  correlation exactly covers invocation/run/work-package/attempt IDs, attempt number,
  role, and expected response schema.
- Capability checks precede fake execution and cover availability, role, read/write,
  structured output, cancellation, timeout, usage, artifacts, and repair. Unsupported
  requests do not consume a step.
- `parse_agent_response_envelope`, correlation validation, and request-semantic validation
  are separate. Normalization converts every rejection to sanitized `INVALID_OUTPUT`.
- The parser rejects oversized bytes before parsing, invalid UTF-8, duplicate keys,
  trailing content, NaN/infinity, non-object JSON, excessive depth/items, unknown schema/
  fields/enums, missing fields, invalid IDs/paths/timestamps, inconsistent statuses, and
  correlation/role/artifact/usage mismatches.
- Agent artifacts are immutable relative references tied to an approved root identity,
  kind, media type, size/completeness, redaction state, and optional SHA-256. The command
  stream-specific absolute artifact contract was not reused. No durable store was added.
- `FakeAgentScenario` is immutable and finite. Steps contain an exact canonical request
  SHA-256, explicit UTC start and millisecond duration, bounded cancellation checkpoints,
  and a typed outcome or bounded raw bytes sent through the production parser.
- Pre-cancellation, incompatibility, unavailability, and request mismatch do not consume
  steps. Timeout/mid-invocation cancellation consume the begun step and record possible
  side effects. Exhaustion is explicit. A new adapter over the same scenario replays from
  step zero; state is isolated and access is locked.
- ADR-0006 records the accepted envelope/parser/fake/artifact decision.

## Tests

The 68 focused tests prove version round trips, canonical serialization, immutability,
unknown/missing/version rejection, identifier/path/time/bound checks, duplicate scope/
context/artifact rejection, reviewer/repair invariants, status/failure/retry consistency,
exact correlation, all parser attacks above, secret removal, all three successful roles,
blocker/failure/unavailable/timeout/pre- and mid-cancellation, unsupported capability/
role, exact request mismatch, ordered repetition, exhaustion, isolated and concurrent
instances, deterministic replay, artifact handling, and lack of run/approval authority.
Architecture tests reject provider/process/network/Git/SQLite/CLI/orchestration imports,
public `Any`, arbitrary fake callbacks, workflow mutation, and approval construction.

## Verification

- Pre-edit baseline: `uv sync --dev`, format, Ruff, mypy, full pytest, and doctor passed;
  268 passed, 1 expected Windows skip.
- `uv run pytest tests/contract/test_agent_contract.py tests/contract/test_agent_architecture.py tests/unit/test_agent_parsing.py tests/unit/test_fake_agent.py` - 68 passed.
- `uv run pytest tests/unit tests/contract tests/integration` - 336 passed, 1 expected
  Windows skip in 143.56 seconds.
- `uv run ruff format --check .` - exit 0; 104 files already formatted.
- `uv run ruff check .` - exit 0; all checks passed.
- `uv run mypy src tests` - exit 0; 62 source/test files, no issues.
- `uv run pytest` - exit 0; 336 passed and 1 expected Windows skip in 148.21 seconds.
- `uv run revanent doctor` - exit 0; Python/Windows, uv, Git, and Codex available;
  OpenCode accurately unavailable.
- Focused security scans - no prohibited imports, unsafe deserialization/construction,
  public untyped port boundary, raw exception/payload leakage, credential literals,
  workflow/approval authority, fake mutation, or nondeterminism; reviewed matches were
  safe internal JSON dictionaries and static validation messages.
- `git diff --check` - exit 0 with LF-to-CRLF normalization warnings only.
- Platform: Windows AMD64, CPython 3.12.11. Fake-provider results are simulated only.
  OpenCode is unavailable; Codex detection is available. No live provider call ran.

## Limitations and next package

P3-001 provides no live provider execution, provider command mapping, durable replay,
artifact persistence, orchestration, validation/approval connection, context selection,
or usage-budget enforcement. Known-value redaction cannot discover unknown or transformed
secrets. Phase 3 remains in progress.

Next: P3-002 - OpenCode and Codex Capability Detection and Adapters. Recommended model:
GPT-5.6 Terra at high reasoning, with Sol review for write/security boundaries. A local
model may implement only bounded fake-executable fixtures after capability mappings are
fixed.
