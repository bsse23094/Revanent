# CLI Reference

Implemented in Phase 0:

- `revanent --help`: list available commands.
- `revanent --version`: print the installed package version.
- `revanent doctor [--strict]`: perform read-only runtime/provider detection. The
  default succeeds when optional providers are absent; strict mode treats missing
  providers as failure. Required runtime gaps always fail.

Planned after the core services exist: `init`, `inspect`, `run`, `resume`, `status`,
`report`, `cancel`, `clean`, `config validate`, and `agents detect`. Commands will be
added only with end-to-end behavior and tests; this file will then document exact
arguments, exit codes, mutation behavior, and artifact locations.
