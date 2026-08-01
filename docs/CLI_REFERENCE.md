# CLI Reference

P6-002-C1 exposes safe runtime control over an initialized project. `report` and cleanup remain
unimplemented and are reserved for P6-002-C2.

## Exit codes

| Code | Meaning |
|---:|---|
| 0 | Command completed successfully. Optional provider absence is successful in default doctor/detection mode. |
| 2 | Invalid configuration or invocation. Typer option parsing also uses its normal distinct validation failure. |
| 3 | Initialization conflict or safety refusal; no user file was overwritten. |
| 4 | Required runtime/configuration dependency gap or a strict provider-detection failure. |
| 5 | Requested run was not found. |
| 6 | Run is blocked or recovery remains unresolved. |
| 7 | Stale/concurrent coordinator request; no work was launched. |
| 8 | Run failed or durable evidence is internally contradictory. |
| 70 | Unexpected internal failure. |

## `revanent init --repository PATH`

Initializes only a clean, non-bare Git working tree. The target is discovered through the
controlled Git boundary. It plans before writing and then creates only a root-level
`revanent.yaml` plus `.revanent/worktrees`, `.revanent/runs`, and `.revanent/state`.

The `.revanent` root must already be ignored. This command never edits `.gitignore`, Git
configuration, branches, commits, worktrees, run state, provider settings, or network state.
An identical generated configuration is reused without rewriting it. A differing file, link,
junction, special file, unignored root, dirty repository, collision, or unexpected entry is
refused with exit code 3.

## `revanent config validate`

```text
revanent config validate --repository PATH [--config revanent.yaml]
                         [--max-total-minutes 1..10080] [--json]
```

Loads only the target repository's root-level `revanent.yaml`, validates schema version 1 and
the effective configuration, and resolves workspace/report/state paths from the target root.
It creates no files, runs no provider, and mutates no Git state. `--max-total-minutes` is the
sole temporary typed override; it cannot enable network, push, merge, arbitrary commands, or a
broader path scope. JSON uses schema version 1 and stdout contains only the JSON document.

## `revanent doctor`

```text
revanent doctor [--repository PATH] [--strict] [--json]
```

Checks Python, platform, uv, Git, optional target repository/configuration, OpenCode, and
Codex. It is read-only. Default mode reports missing optional OpenCode as `UNAVAILABLE` without
failure. `--strict` treats unavailable or incompatible provider capability surfaces as exit 4.
Checks are stably ordered and report only bounded facts, never secret values, raw provider
output, raw environment values, or tracebacks. JSON uses schema version 1.

## `revanent agents detect`

```text
revanent agents detect [--repository PATH] [--strict] [--json]
```

Performs only accepted P3-002 version/help capability inspection through the controlled runner.
It reports OpenCode builder capability and separate Codex review/repair capability facts. It
does not submit a model request, inspect credentials, install/update a provider, or trust a
repository-local executable. Default mode reports unavailable providers; `--strict` exits 4.

## Runtime commands (P6-002-C1)

```text
revanent run --repository PATH --task-file TASK.json [--work-package ID] [--json]
revanent resume RUN_ID --repository PATH [--expected-revision N] [--json]
revanent status RUN_ID --repository PATH [--json]
revanent cancel RUN_ID --repository PATH [--expected-revision N] [--json]
```

`run` requires compatible configured builder/reviewer capabilities, then accepts only a regular
UTF-8 JSON `TaskSpecification` of at most 64 KiB at a normalized repository-relative path. Absolute,
parent-traversing, symlink, junction/reparse, special, oversized, invalid-UTF-8, malformed, unknown,
or unsupported-schema input is refused. The task schema is the strict domain schema: version, task
ID, objective, allowed paths, acceptance criteria, and its existing optional bounded fields. Task
prose is data and cannot enable network, publication, cleanup, arbitrary commands, approval bypass,
or broader filesystem authority.

Run creation atomically persists revision-zero Run data and its immutable full repository identity,
deterministic worktree ID, repository-relative target, and branch before context selection,
worktree creation, reservation, provider invocation, or validation. A required provider gap exits 4
before that commit or any launch. Every later command rediscovers and compares the full typed
repository identity; operations after workspace creation also require the matching active ownership
record and live Git worktree identity. Mismatch is `BLOCKED` and does not reconcile or mutate.

`resume` first invokes the coordinator's reconciliation boundary. Completed context, workspace,
agent, validation, and settlement evidence is reused. Missing settlement for a trusted outcome is
recovered idempotently; ambiguous mutating initiation and unresolved reservations are preserved and
block instead of replaying. Terminal Runs are stable no-ops, and stale/concurrent losers launch
nothing.

`status` reads only canonical Run, event, orchestration, context-manifest metadata, usage,
reservation, and owned-worktree evidence. Its schema-v1 snapshot includes safe identity/reference,
stage/latest event, per-role attempt counts/status, context byte metadata, validation totals, review
and ApprovalGate facts, provenance-separated usage, currency-separated remaining budgets, active/
unresolved reservations, cancellation/ambiguity, safe artifact references, evidence completeness,
and stable reason/contradiction codes. Contradictory evidence is `INVALID_EVIDENCE` (exit 8). Status
does not probe providers, reconcile, settle, transition, repair storage, or write.

`cancel` uses only the coordinator state machine. It is idempotent, honors expected revisions, and
does not claim a process was killed. It never launches a provider/validator, releases ambiguous
reservations, cleans/removes a worktree, or deletes artifacts/evidence. JSON is canonical and omits
task/context/source/provider/command bodies, secrets, raw exceptions, and absolute paths where a
safe relative reference suffices.

## `revanent report`

```text
revanent report RUN_ID --repository PATH [--format json|markdown] [--output PATH] [--json]
```

Reports read existing canonical durable evidence only. They do not reconcile, settle telemetry,
transition a Run, invoke a provider/validator, mutate Git/worktrees, or repair inconsistent state.
The schema-v1 JSON object is canonical; Markdown is a deterministic escaped rendering of that same
object. `--json` is an alias for `--format json` and cannot be combined with `--format markdown`.
Without `--output`, stdout is the only output and no report artifact is written.

`--output` must be a report-root-relative path. Absolute/traversing, `.git`, link/reparse, special,
or unsafe parent paths are refused, as are collisions with differing bytes. An identical existing
artifact is reused; no overwrite/force option exists. New output uses a private temporary file,
flush/fsync, and create-exclusive finalization. The report manifest's SHA-256 digest is integrity
metadata, not a signature.

Statuses are `COMPLETE`, `COMPLETE_WITH_WARNINGS`, `INCOMPLETE`, `INVALID_EVIDENCE`, `BLOCKED`,
`NOT_FOUND`, `OUTPUT_CONFLICT`, and `INTERNAL_FAILURE`. Complete and warning reports exit 0;
incomplete/blocked exit 6; invalid evidence exits 8; missing Runs exit 5; output conflicts exit 3;
unexpected internal failure exits 70. Reports omit raw task/context/source/provider/command bodies,
credentials, environment values, raw exceptions, and raw command/provider output. Active Runs are
incomplete; an APPROVED Run is only reported complete after independent ApprovalGate, validation,
review, correlation, and ambiguity checks. There is no cleanup command.

## P7 live execution policy

Normal `run` remains blocked until project policy explicitly enables network execution plus the
OpenCode-builder and Codex-reviewer roles. Codex repair also requires its independent write-repair
flag. Provider installation, credentials, models, network access, and pricing are never inferred or
configured by Revanent. Live certification is a pytest-only C1 harness and is not a general CLI
bypass.
