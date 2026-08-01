# ADR-0012: Safe project initialization and inspection CLI

- **Status:** Accepted
- **Date:** 2026-08-01

## Context

The Phase 1 through 5 libraries were usable only through explicit composition. P6-001 must
make safe setup, configuration validation, runtime diagnostics, and provider capability
inspection usable without turning the CLI into an orchestration, Git, command, or provider
policy owner. Initialization must not overwrite user files or silently dirty arbitrary
repositories, and normal diagnostics must remain useful when an optional provider is absent.

## Decision

The Typer CLI is a presentation/input-adaptation layer over typed P6 application services.
Only `init`, `doctor`, `config validate`, and `agents detect` are exposed. Run, resume,
status, report, cancellation, cleanup, and workflow execution remain absent until P6-002.

Project configuration is exactly the root-level `revanent.yaml` file of an explicitly resolved
target repository. Loading uses the existing bounded safe YAML loader and immutable schema-v1
model. Precedence is built-in model defaults, project YAML, schema-declared secret references
(none in schema v1), then the sole typed `--max-total-minutes` validation override. Arbitrary
environment overlays and unsafe CLI overrides do not exist. Workspace, report, and state roots
are resolved from the repository root, are distinct, reject absolute/traversing/.git/link/
junction escapes, and never depend on the caller CWD.

`init` first constructs a deterministic side-effect-free plan. It requires a non-bare,
clean, operation-free repository discovered through the existing controlled Git port, except
for the single root-level generated configuration file needed for a repeated init conflict
decision. In-repository `.revanent` roots must already be ignored; Revanent never edits
`.gitignore`. The plan permits only `revanent.yaml`, `.revanent/worktrees`, `.revanent/runs`,
and `.revanent/state`. Missing directories use restrictive creation; a completed temporary
configuration file is linked create-exclusively into place so no existing file is overwritten
or observed partially. Identical generated configuration and compatible owned directories are
reused; differing files, links/junctions, special files, unexpected owned-root entries,
unignored roots, or path collisions refuse. Partial setup preserves safe created resources for
a later idempotent retry and deletes only its own temporary file.

Doctor and provider detection are read-only. They compose the existing controlled runner with
a selected host baseline, exclude repository-local executables, and call only runtime version
or P3 version/help probes. OpenCode absence is an optional `UNAVAILABLE` result by default.
`--strict` makes unavailable or incompatible providers fail. Codex review and repair surfaces
remain separately reported. No provider request, credential check, network access, Git mutation,
or run-state creation occurs.

Exit codes are frozen: `0` success, `2` invalid configuration/invocation, `3` initialization
refusal or conflict, `4` required runtime or strict provider gap, and `70` unexpected internal
failure. `doctor`, `config validate`, and `agents detect` provide strict schema-version-1 JSON
when `--json` is selected; otherwise Rich renders only typed, bounded human results.

## Consequences

P6-001 makes initialization and inspection usable without granting workflow authority. A
repository must already ignore its owned in-tree state root and configuration remains a visible
user-controlled file, so init intentionally refuses a dirty source rather than guessing.
Schema v1 has no secret-reference field, pricing, provider authentication, or generalized
configuration override. Live-provider certification, durable run selection, resume/status/
report output, and evidence reports remain P6-002 or later work.
