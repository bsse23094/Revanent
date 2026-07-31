# P2-001: Controlled Commands and Path Policy

- **Status:** COMPLETE (2026-07-30)
- **Objective:** Provide the sole bounded subprocess runner and normalized path/executable policy.
- **Requirements:** SEC-001, SEC-002, SEC-003, SEC-004, NFR-007.
- **Dependencies:** P1-001.
- **In scope:** argument-list runner, cwd/allowlist policy, environment filter, redaction, timeout,
  cancellation, bounded capture/artifacts, Windows/POSIX path policy and fake executables.
- **Out of scope:** Git worktrees and provider-specific command construction.
- **Steps:** define port/results; implement policies; add adversarial tests for injection, traversal,
  links/junctions, case, secrets, timeout, cancellation, and output truncation; document trust limits.
- **Security constraints:** no shell by default; fail closed; executable and cwd must be authorized.
- **Acceptance criteria:** malicious fixtures cannot escape policy; secrets are redacted; children stop;
  full output is bounded/referenced; platform integration tests pass.
- **Verification:** canonical gates plus `uv run pytest tests/unit tests/integration -k command`.
- **Completion evidence:** Immutable version-1 command port contracts with typed environment
  overrides instead of raw boundary dictionaries, explicit resource,
  executable, path, environment, and redaction policies, cooperative cancellation, and the sole
  production local subprocess adapter are implemented. The adapter uses argument lists with
  `shell=False`, distinct bounded streams, redacted atomic overflow artifacts, monotonic duration,
  UTC timestamps, and normalized terminal failures. Doctor uses the controlled runner. Seventy-two
  focused command tests pass on Windows, including actual junction escape, literal metacharacter,
  filtered environment, direct-child cleanup, timeout/cancellation race, invalid-byte, concurrent
  stream, secret, and artifact cases. The full 188-test suite passes with no skips.
- **Risks:** Windows process-tree cancellation and junction behavior.
- **Recommended model/effort:** GPT-5.6 Sol, xhigh.

## Implemented guarantees

- Executable requests accept only configured simple names; ordered absolute candidates determine
  resolution, current-directory and repository-local PATH discovery can be excluded, and the
  resolved identity is returned.
- Existing working directories must resolve inside approved roots. Relative operations reject
  absolute paths and parent traversal; link/junction targets and sibling-prefix paths are checked
  through structural resolved containment. UNC and filesystem roots require explicit policy.
- Child environments contain only a bounded explicit baseline and allowlisted command overrides.
  Windows keys normalize case-insensitively. Sensitive-shaped keys require explicit authorization
  and their values are automatically added to redaction.
- Timeout begins immediately before launch. A pre-cancelled request does not launch. Cancellation
  has precedence over timeout when both are observed in one monitor iteration. Termination escalates
  once within configured grace.
- stdout and stderr are concurrently drained and independently byte-bounded. Counters are measured
  before UTF-8 replacement decoding. Redaction expansion is bounded and separately reported.
  Approved overflow artifacts are redacted before atomic persistence and report complete/truncated
  state and source/redacted/stored byte counts.

## Verified limitations

This package is policy-controlled local execution, not isolation. Authorized tools can use their
ambient host permissions and launch descendants. POSIX process-group termination is implemented;
Windows guarantees only direct-child termination without an additional dependency. Windows batch
launchers are accepted only through explicit extension policy, but Windows itself may apply command
processor parsing even with `shell=False`. Resolved path checks cannot close malicious concurrent
filesystem replacement races. Redaction covers configured values and documented credential forms,
not every possible secret. Command artifact references are not yet wired into durable run state.

The completed suite ran on Windows CPython 3.12.11. Cross-platform branches and Linux CI tests are
present, but a usable POSIX Python environment was unavailable locally; WSL exposed only the
Docker Desktop utility distribution without Python, and Docker Engine was unavailable.

## Verification evidence

- Pre-edit baseline: 27 packages resolved/audited; Ruff format/check clean; mypy clean across 29
  files; 119 tests passed; doctor succeeded.
- Focused P2-001 command suite: 72 passed, 0 skipped.
- Unit/contract/integration and full suites: 188 passed, 0 skipped.
- Package selector `tests/unit tests/integration -k command`: 44 passed, 117 deselected after a
  repaired Windows startup-timing test failure; the exact repair evidence is in project state.
- Final canonical commands, security scans, doctor output, and `git diff --check` are recorded in
  `docs/PROJECT_STATE.md`.
- **Next package:** P2-002 — Safe Git Worktrees.
