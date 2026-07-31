# P5-001: Deterministic Context Packages

- **Status:** COMPLETE (verified 2026-07-31)
- **Objective:** Select minimal task-specific evidence with a reproducible inclusion manifest.
- **Requirements:** FR-003, SEC-004, SEC-006, SEC-007, NFR-002, NFR-003, NFR-007,
  OPS-001, OPS-002, OPS-007.
- **Dependencies:** P4-002.
- **In scope:** typed explicit/diff/validation/review/attempt/decision/governance/artifact
  evidence; bounded Python import and exact-name test expansion; scope, path, race, secret,
  provenance, priority, truncation, deduplication, byte measurement, canonical manifests,
  AgentRequest projection, and CONTEXT_PREPARING integration.
- **Out of scope:** embeddings, vector database, semantic service, universal language parsing,
  general artifact store, token/cost telemetry, user-facing resume/report commands, live
  providers, and benchmark savings claims.
- **Security constraints:** forbidden scope overrides inclusion; all filesystem reads use the
  bounded consistency reader; repository/provider text remains untrusted; raw secret-bearing
  content is neither retained nor persisted; required unsafe evidence fails explicitly.
- **Acceptance criteria:** identical authorized inputs produce identical ordered manifests;
  required evidence fits completely or fails; provider launch requires a matching complete
  manifest; context bodies do not enter SQLite; representative fixture reduction is measured
  in bytes without token/cost claims; adversarial Windows link/junction and portable path,
  race, secret, injection, artifact, deduplication, and orchestration tests pass.
- **Implemented evidence:** `revanent.ports.context` owns schema version 1. The local selector
  discovers all approved typed sources, enforces TaskSpecification scope, expands bounded
  Python dependencies and `test_<module>.py` tests, validates correlated approved artifacts,
  redacts or refuses credential material, detects before/after file changes, preserves
  authority/trust/role provenance, applies deterministic required/preferred/optional eviction
  and UTF-8 head/tail truncation, deduplicates only equal-authority/trust content, and emits a
  metadata-only canonical manifest with exact local byte accounting. Agent bodies use the
  existing `AgentRequest.context` field. P4 persists context intent/manifest outcomes in
  CONTEXT_PREPARING through SQLite migration 3 and blocks/fails before workspace/provider
  execution when evidence is unsafe, incomplete, stale, or mismatched.
- **Verification:** `uv sync --dev`, Ruff format/check, mypy, focused P5 (96 passed), grouped
  canonical, and top-level full suite (each 575 passed, 1 expected Windows filename skip),
  doctor, diff check, and targeted architecture/security scans passed. No live provider,
  network, or Git mutation occurred.
- **Risks/limitations:** Python-only static imports, exact-name test convention, explicitly
  named ADRs, no snapshot isolation, no universal secret detection, and no general durable
  context-body artifact store. Process continuation re-reads and must reproduce the manifest.
- **Recommended model/effort:** GPT-5.6 Terra, high; Sol final path/security/orchestration audit.
- **Next package after completion:** P5-002 — Usage Telemetry and Budget Enforcement.
