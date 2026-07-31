# ADR-0007: Frozen Provider CLI Surfaces and Permission-Separated Adapters

- **Status:** Accepted
- **Date:** 2026-07-30

## Context

OpenCode and Codex CLIs can change flags and event formats independently of Revanent.
Finding an executable is therefore insufficient evidence that it is safe or compatible.
Review must also be unable to acquire repair authority through a configuration mistake.

## Decision

Provider detection executes only bounded `--version`, top-level `--help`, and required
subcommand `--help` requests through `CommandRunner`. A frozen adapter accepts only an
exact supported version family and the required help tokens. Results distinguish
`AVAILABLE`, `UNAVAILABLE`, and `INCOMPATIBLE`; the last two map to the existing P3-001
`UNAVAILABLE` capability state with explicit compatibility metadata and a reason.

OpenCode v1 support is a BUILDER-only `run --format json` stdin/JSONL surface. Because
OpenCode is absent on the development machine, this surface is verified by finite fake
executables only and local capability remains unavailable.

Codex support is frozen to the locally inspected `codex-cli 0.146.*` `exec` surface.
Review uses top-level `--ask-for-approval never`, `--ignore-user-config`, `--ephemeral`,
`--sandbox read-only`, JSONL, and stdin. Repair is a separate adapter and argument path,
uses `--sandbox workspace-write`, and requires both a typed `REPAIRER` request and an
explicit `write_authorized=True` constructor decision. Neither adapter accepts arbitrary
extra arguments.

Provider JSONL framing is parsed strictly before the existing P3-001 envelope parser.
Unknown events, duplicate keys, malformed/truncated streams, absent or contradictory
terminal events, and untrusted provider artifact paths fail closed. Command failures are
mapped without exposing `CommandResult`. Write-capable uncertain outcomes retain
`POSSIBLE` side effects; adapters never clean or roll back a worktree.

## Consequences

- CLI drift becomes an actionable incompatibility rather than a guessed invocation.
- Review/write authority is visible in separate capabilities, types, flags, and tests.
- Compatible future versions require an explicit adapter update and regression fixtures.
- Provider-returned paths do not become Revanent artifacts in version 1; only controlled,
  redacted command artifacts can support a future trusted projection.
- Local flags strengthen review isolation but are not an operating-system sandbox proof.
- Live/model execution remains outside P3-002 and is not implied by safe help inspection.
