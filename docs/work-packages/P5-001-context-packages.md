# P5-001: Deterministic Context Packages

- **Status:** PLANNED
- **Objective:** Select minimal task-specific evidence with a reproducible inclusion manifest.
- **Requirements:** FR-003, SEC-006, SEC-007, NFR-003, NFR-007.
- **Dependencies:** P4-002.
- **In scope:** repository summary, file-tree filtering, explicit/diff/import/test/doc/failure/finding
  heuristics, deduplication, bounded excerpts, rationale manifest, fixture tests.
- **Out of scope:** embeddings, vector database, semantic service, benchmark claims.
- **Steps:** define manifest schema; implement ordered heuristics; enforce binary/size exclusions;
  test determinism and required-file retention; measure bytes against full-tree baseline.
- **Security constraints:** exclude secrets and forbidden paths; repository text remains untrusted.
- **Acceptance criteria:** same inputs yield identical manifest; fixture-required files remain; irrelevant
  content reduction is measured and no unsupported percentage is claimed.
- **Verification:** canonical gates plus `uv run pytest tests/unit tests/integration -k context`.
- **Completion evidence:** Not started.
- **Risks:** naive import parsing and over-aggressive exclusion.
- **Recommended model/effort:** GPT-5.6 Terra, high.
- **Next package:** P5-002 — Usage and Budget Telemetry.
