# Work Packages

Work packages are bounded delivery contracts. Status is one of `PLANNED`,
`IN_PROGRESS`, `BLOCKED`, `COMPLETE`, or `SUPERSEDED`. Completion requires code,
tests, exact successful verification, documentation, an explained diff, completion
evidence, and an updated `PROJECT_STATE.md`.

Future briefs may be narrowed before starting but must not silently expand. Common
safety gates for every package: no secrets, no destructive Git, no push/merge/publish,
no unrelated edits, and no claimed checks that did not execute.
