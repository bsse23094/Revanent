# Configuration

Project configuration is YAML with a required integer `schema_version`. The P1-001
implementation accepts schema version 1 and uses immutable Pydantic models that reject
unknown keys/versions, invalid limits, unsafe path combinations, duplicate validation
commands, and unsupported provider modes before any agent invocation.

Version 1 has these required top-level sections: `project`, `workspace`, `builder`,
`reviewer`, `validation`, and `policy`. `budgets` and `reporting` have safe defaults.
The example in `revanent.example.yaml` is validated by the contract test suite.
Provider values are currently `opencode` for the builder and `codex` for the reviewer;
reviewer mode is `review_only` (with zero repairs) or `review_then_repair`. P3-002 does
not wire this YAML to live adapters: constructors still require explicit verified
capabilities, controlled runner policy, and (for repair) write authorization. P6-001
owns that safe user-facing wiring.

## Precedence

From lowest to highest: built-in safe defaults, project YAML, explicitly supported
environment references for secrets, and allowlisted CLI overrides. Arbitrary nested
environment overlays are not supported. CLI overrides cannot enable push, merge,
network, destructive Git, or broader filesystem access without an explicit approval
workflow. Effective configuration is validated and recorded with secrets removed.

Relative paths resolve from the target repository root, not the caller's current
directory. Paths are normalized before policy comparison. Configuration never embeds
provider tokens. Version 1 defines no credential field. P3-002 provider constructors may
receive typed allowlisted environment values from an external filtered boundary; names
must also be allowed by `AgentRequest`, values never enter YAML/request/prompt/arguments,
and configured sensitive values must be supplied to runner and parser redaction.

Schema version 1 normalizes Windows separators and rejects absolute paths, parent
traversal, and repository-wide allowed globs. Workspace/report directories must be
distinct and cannot be under `.git`. `allow_push` and `allow_merge` are fixed to false;
the configuration model cannot itself grant publishing authority. Detailed link and
case-normalization enforcement remains in the P2-001 path-policy adapter.

P2-002 implements the Git/worktree library boundary but does not yet wire project YAML
to its constructor. Its concrete adapter receives explicit already-resolved worktree and
ownership-state roots plus protected-branch policy. When the schema-v1 relative
`workspace.root` is wired by P6-001, an in-repository root is valid only when its
containing path is already ignored; Revanent will not modify `.gitignore`. The current
Git library defaults protect `main`, `master`, `release/*`, `protected/*`, and a locally
discoverable `origin/HEAD`, while requiring task branches under `revanent/`. A future
configuration schema change is required before those library policy values become YAML
settings; they are not accepted as unknown schema-v1 keys today.

Schema evolution uses explicit versions and migration/rejection rules documented in
an ADR. Older run state is never silently interpreted as the latest schema.

P5-001 does not add YAML keys. Library composition constructs strict context requests from the
validated `TaskSpecification`, durable run/work-package identity, verified source root, typed
local evidence, injected timestamp, and explicit `ContextLimits`. Unknown context fields or
versions are rejected. P6-001 may expose reviewed safe context-limit settings later only through
an explicit configuration-schema change; repository files and provider output cannot override
scope, trust, artifact roots, secret policy, or required-evidence behavior.

P5-002 enforces the existing `budgets.max_remote_tokens` and
`budgets.max_estimated_cost_usd` values as optional hard limits. Absence means unlimited, not
zero. Because schema version 1 has no reviewed finite per-invocation token/cost ceiling or rate
table, setting either hard external limit currently fails closed before agent invocation and
consumes no attempt. No pricing is embedded or fetched, and repository/provider data cannot
override these values. Duration and role-attempt limits continue to come from the immutable
durable `Run` budget snapshot. P6-001 must preserve these semantics when wiring configuration.

## P6-001 discovery, validation, and initialization

The accepted project configuration filename is exactly `revanent.yaml` at the discovered target
repository root. `revanent config validate --repository PATH` does not search arbitrary parent
directories and an explicit `--config` may name only that root-level file. The loader rejects
links/junctions, special files, files above 256 KiB, malformed YAML, unsupported schema versions,
unknown keys, and validation errors without echoing input values.

P6-001 freezes the precedence order: immutable schema defaults, project YAML, explicitly
declared schema secret references, then typed allowlisted CLI overrides. Schema version 1 declares
no secret-reference field, so no environment value is read into effective configuration. Arbitrary
environment overlays are unsupported. The sole current override is
`config validate --max-total-minutes`; it is validated through the complete schema and cannot
change provider, path, network, Git publication, command, or approval policy.

Workspace, report, and Revanent state roots resolve from the target repository root regardless
of the caller CWD. They must be distinct, repository-relative, and free of absolute paths,
parent traversal, `.git` components, symlink/junction escapes, and sibling-prefix ambiguity.
The default schema-v1 template is constructed and validated by production code, not copied from
an unvalidated CLI string.

P6-002 report output uses the configured report root only when `revanent report --output` is
explicit. The output name remains relative to that root; it does not authorize absolute paths,
overwrites, or any configured path escape.

Live workflow execution is default-off. `policy.allow_network`,
`policy.allow_live_opencode_builder`, and `policy.allow_live_codex_reviewer` must all be explicitly
true before runtime composition authorizes provider stdin. `policy.allow_codex_write_repair` is a
separate additional repair-role decision; reviewer authorization never grants write access.

`revanent init` writes that canonical template only to an absent root-level `revanent.yaml`.
It reuses an identical file without rewriting it and refuses a differing or unsafe existing file.
It creates only `.revanent/worktrees`, `.revanent/runs`, and `.revanent/state`, after confirming
the `.revanent` root is already ignored. It never changes `.gitignore`.
