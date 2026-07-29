# Roadmap and Work-Package Decomposition

Packages run in dependency order. A later package may be refined before execution,
but scope expansion requires an updated package and project state.

| Package | Phase | Objective | Depends on | Primary requirements |
|---|---:|---|---|---|
| P0-001 | 0 | Authoritative foundation, tooling, doctor, CI | None | NFR-001/005/006, OPS-004 |
| P1-001 | 1 | Versioned config/domain schemas and central state machine | P0-001 | FR-001/008/010, NFR-002/004, OPS-006/007 |
| P1-002 | 1 | SQLite repository, events, migrations, deterministic reload | P1-001 | FR-011, OPS-001/002/007 |
| P2-001 | 2 | Controlled command runner, redaction, path/executable policy | P1-001 | SEC-001/002/003/004, NFR-007 |
| P2-002 | 2 | Git inspection and owned worktree lifecycle | P1-002, P2-001 | FR-002/004, SEC-005, OPS-003 |
| P3-001 | 3 | Provider-neutral contracts and deterministic fake adapter | P1-001, P2-001 | FR-005/006, NFR-003 |
| P3-002 | 3 | OpenCode/Codex capability detection and adapters | P3-001 | FR-006/014, SEC-006/007 |
| P4-001 | 4 | Validation and structured review gates | P2-001, P3-001 | FR-007/008/015 |
| P4-002 | 4 | Bounded orchestration and explicit repair policy | P1-002, P2-002, P3-001, P4-001 | FR-009/010/011 |
| P5-001 | 5 | Deterministic context selection and manifest | P4-002 | FR-003, SEC-007 |
| P5-002 | 5 | Usage telemetry and budget enforcement | P5-001 | FR-010, NFR-008 |
| P6-001 | 6 | Config/init/doctor and provider-detection UX | P2-002, P3-002 | FR-013/014, OPS-006 |
| P6-002 | 6 | Run/resume/status/report CLI and reports | P4-002, P5-002, P6-001 | FR-011/012/013, OPS-001/002/005 |
| P7-001 | 7 | Live opt-in integration and reliability/security hardening | P6-002 | SEC-008, OPS-004/008 |
| P8-001 | 8 | Fixture benchmark and efficiency evaluation | P7-001 | NFR-008, OPS-001 |
| P8-002 | 8 | Packaging, release certification, installation docs | P8-001 | NFR-001/005, OPS-004/007 |

## Phase exit gates

- **Phase 0:** docs authoritative; package imports; format/lint/types/tests pass.
- **Phase 1:** invalid transitions fail; persisted state and events reload; migrations documented.
- **Phase 2:** Windows/POSIX temporary-repository tests prove safe command/worktree behavior.
- **Phase 3:** fake outcomes are deterministic; unavailable live providers yield actionable blockers.
- **Phase 4:** complete fake loop passes; approval cannot bypass validation; limits hold.
- **Phase 5:** context is deterministic and measurably smaller without losing required files.
- **Phase 6:** new repositories initialize and interrupted runs resume with understandable reports.
- **Phase 7:** one evidenced local-builder-to-Codex run; recovery paths pass; no high security risks.
- **Phase 8:** benchmark results and reproducible release artifact meet the release checklist.

Package briefs live in `docs/work-packages/`. Each is revalidated against repository
evidence before its status changes to `IN_PROGRESS`.
