# Model Routing

Routing selects the lowest-cost model likely to succeed and records the reason.
Capability detection and measured past attempts override model-name assumptions.

| Task class | First pass | Codex role | Typical effort | Escalation |
|---|---|---|---|---|
| Mechanical, bounded edits | local builder | focused review | low/medium | repeated defect |
| Routine features/tests/CLI | local builder or Terra | review/repair | medium/high | cross-module failure |
| Architecture, recovery, Git safety | Sol | implementation/review | high/xhigh | security/release gate |
| Security or final release | capable local analysis only | Sol authority | xhigh/max | unresolved high risk |

Local builder repair is preferred for mechanical in-scope findings without repetition
or security impact and with sufficient budget. Codex repair is preferred for repeated
defects, invalid output, cross-module reasoning, architecture, concurrency,
persistence, migrations, security, or recovery. Missing credentials/software,
corruption, essential product decisions, forbidden action, or exhausted budgets stop
as `BLOCKED` or the configured limit outcome.

Multi-agent/ultra execution is reserved for independently reviewable tracks such as
release, implementation, and security audits; it is not the ordinary coding default.
Every work-package handoff states model, effort, local-builder scope, reviewer scope,
expected risk, and an exact continuation prompt.

Live certification never infers a model from executable presence or credentials. Each role requires
its own explicit model option. OpenCode cannot be silently replaced by Codex as builder, and Codex
review authorization does not authorize repair. C1 attempted `gpt-5.6-sol` only under separate
one-call reviewer and repair authorizations; neither response reached certification success.
