# Operations

Revanent stores project-local state under `.revanent/`, which is ignored by Git.
Each run owns a stable directory containing `run.json`, `events.jsonl`, task and
context manifests, attempt/validation/review artifacts, and final JSON/Markdown
reports. Artifact writes are atomic where practical and carry schema versions.

Operators use `doctor` before live runs. A run reports concise progress while durable
events remain authoritative. Cancellation first signals the in-flight child, waits a
bounded grace period, then records the reconciled outcome. Resume verifies repository
identity, base commit, worktree ownership, configuration compatibility, and last
side-effect evidence before continuing.

Failed worktrees are preserved by default for diagnosis. Clean operations enumerate
and verify Revanent ownership and require explicit confirmation for material removal.
Back up `.revanent/runs` when its audit history matters; SQLite and artifacts must be
copied from a quiescent run or through a future supported export operation.
