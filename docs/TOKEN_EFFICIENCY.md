# Token Efficiency

Context minimization is deterministic and evidence-preserving. Initial selection uses
explicit paths, current diff, direct imports/dependencies, corresponding tests,
governing architecture/requirements, validation failures, prior findings, and
unresolved decisions. Each included item records its reason. Binaries, dependencies,
generated files, irrelevant history, duplicate documentation, and unbounded command
logs are excluded or referenced.

Telemetry records context bytes and, where available, input, cached input, output,
reasoning tokens, estimated cost, wall time, and local/remote attempts. Each value is
labeled measured, provider-reported, estimated, or unavailable.

Benchmarks compare direct Codex execution with Revanent on multiple fixture tasks.
They report median and range for success, defects, cycles, tokens, cost, runtime,
unnecessary file changes, interventions, and post-approval regressions. No savings
percentage is claimed before repeatable measurement.
