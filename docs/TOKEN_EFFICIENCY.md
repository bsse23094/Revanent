# Token Efficiency

Context minimization is deterministic and evidence-preserving. P5-001 selection uses typed
explicit paths, current change/diff paths, bounded direct Python imports, exact-name tests,
explicit governing documents, validation failures, unresolved findings/attempts/decisions,
and approved artifacts. Required evidence must fit completely; stable priority evicts lower
preferred/optional evidence. Binary, generated/cache/dependency, unsafe, secret-bearing,
duplicate, and unbounded material is excluded or referenced with a reason. The manifest
measures authorized source, retained, excluded, truncated, duplicate-avoided, and baseline
bytes plus a local retained ratio. It makes no token, cost, or savings claim.

P5-002 records exact local context bytes separately from structured provider token usage.
Input, output, and total tokens are `PROVIDER_REPORTED` when present and otherwise
`UNAVAILABLE`; unsupported cached/reasoning categories are rejected rather than inferred.
Validation duration and role attempts are `MEASURED`. Cost is `ESTIMATED` only with a
configured Decimal estimator identity; no current pricing is bundled or fetched, so cost is
otherwise `UNAVAILABLE`. Revanent makes no provider token-savings or actual-billing claim.

P6-001 setup and capability checks do not estimate usage, pricing, or model efficiency. They run
only bounded local version/help probes and keep their diagnostics separate from telemetry.

P6-002 reports preserve the same telemetry provenance: local bytes are never converted to tokens,
provider-reported values remain provider-reported, estimates remain estimates, and unavailable or
unresolved values stay explicit. Report digests measure report bytes only; they are neither billing
evidence nor a provider-output claim.

P7 live authorization requires a finite local token ceiling and estimated-cost ceiling but does not
pretend the provider can always hard-stop at those values. Provider usage remains
`PROVIDER_REPORTED` only when the strict response supplies it; the C1 Codex responses were rejected,
so no live token or billing values were recorded. No pricing table or lookup was added.

Benchmarks compare direct Codex execution with Revanent on multiple fixture tasks.
They report median and range for success, defects, cycles, tokens, cost, runtime,
unnecessary file changes, interventions, and post-approval regressions. No savings
percentage is claimed before repeatable measurement.
