# Release Process

Revanent uses an unreleased `0.1.0.dev0` version during foundation work. A release
requires an approved release work package, clean explained Git state, locked
dependencies, all offline gates on supported platforms, live evidence where required,
security review with no critical/high finding, changelog and limitations, build and
install smoke tests, and reproducible artifacts.

The project will use Semantic Versioning after the first release. Tags, publication,
and remote pushes are human-authorized actions and are never performed merely because
the test suite passes. Rollback retains the previous artifact and documents any state
schema compatibility constraints.
