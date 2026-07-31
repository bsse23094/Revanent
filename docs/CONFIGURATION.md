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
