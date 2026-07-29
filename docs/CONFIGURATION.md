# Configuration

Project configuration is YAML with a required integer `schema_version`. Pydantic
models will reject unknown versions, invalid limits, unsafe path combinations, and
unsupported provider modes before any agent invocation. `revanent.example.yaml` is
illustrative until P1-001 freezes the schema.

## Precedence

From lowest to highest: built-in safe defaults, project YAML, explicitly supported
environment references for secrets, and allowlisted CLI overrides. Arbitrary nested
environment overlays are not supported. CLI overrides cannot enable push, merge,
network, destructive Git, or broader filesystem access without an explicit approval
workflow. Effective configuration is validated and recorded with secrets removed.

Relative paths resolve from the target repository root, not the caller's current
directory. Paths are normalized before policy comparison. Configuration never embeds
provider tokens; it may name an environment variable whose value remains external.

Schema evolution uses explicit versions and migration/rejection rules documented in
an ADR. Older run state is never silently interpreted as the latest schema.
