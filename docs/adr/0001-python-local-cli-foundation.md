# ADR-0001: Python local CLI foundation

## Context

The MVP must be Windows-first, local, typed, testable, and avoid hosted infrastructure.

## Decision

Use Python 3.12+, uv, a `src/` package, Typer/Rich, Pydantic, pytest, Ruff, and strict
mypy. Use native subprocess/Git adapters and YAML project configuration. Keep the MVP
a single local process with adapter boundaries.

## Alternatives considered

TypeScript offered strong CLI libraries but a less direct fit for the requested local
Python tooling. A web service, database server, message broker, or container runtime
would add deployment and trust complexity without MVP value. Supporting Python 3.11
would forgo useful modern typing and conflict with the stated baseline.

## Consequences

Contributors need uv and a compatible Python. Provider CLIs remain separate
dependencies. Cross-platform subprocess/path tests are mandatory.

## Status

Accepted — 2026-07-29.
