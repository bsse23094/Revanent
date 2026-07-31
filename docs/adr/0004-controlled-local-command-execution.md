# ADR-0004: Controlled local command execution

## Context

Later Git, validation, OpenCode, and Codex adapters must invoke untrusted local tools
without duplicating process policy or exposing raw subprocess objects. Command output
can contain secrets or exhaust memory and disk; executable search, environment
inheritance, path comparison, timeout, and cancellation also differ across Windows
and POSIX.

## Decision

Use a provider-independent `CommandRunner` port with immutable version-1 request,
result, status, typed environment-entry/override, failure, captured-output, and
artifact-reference contracts. The local
adapter is the sole production `subprocess` owner. It passes an executable plus an
ordered argument list with `shell=False`, an explicit resolved working directory, and
a deliberately constructed environment.

Executable policy maps simple configured names to ordered absolute candidate paths.
Search-path construction ignores empty, relative, UNC, and explicitly excluded
repository entries. The result records the resolved executable identity. Filesystem
policy resolves approved roots and paths with `pathlib`, uses structural containment,
rejects traversal and unauthorized UNC/filesystem roots, and separately authorizes
artifact directories. Environment policy starts from a bounded explicit baseline,
normalizes Windows keys, applies allowlisted overrides, and rejects sensitive-shaped
keys unless explicitly authorized for redaction.

Runner-specific policy ceilings further restrict the hard request bounds for timeout,
stdin, stdout, stderr, and artifacts. stdout and stderr are drained concurrently and
bounded independently before UTF-8 replacement decoding. Configured secrets,
sensitive environment values, authorization headers, and common credential-shaped
assignments are redacted before public results or atomic artifact writes. Source and
redacted-representation truncation are reported separately.

Timeout starts immediately before the process launch call. Pre-cancellation prevents
launch; during a timeout/cancellation race cancellation has precedence when both are
observed in the same polling iteration. POSIX launches a new session and terminates
its process group. Windows creates a new process group but guarantees termination only
for the direct process with the standard library.

## Alternatives considered

Ad hoc `subprocess.run` calls would duplicate policy and leak provider concerns.
Shell command strings would make argument boundaries platform-dependent. Forwarding
the host environment would expose unrelated credentials. Unbounded `communicate` or
full in-memory capture would permit resource exhaustion. Adding containers, a remote
runner, or a platform process-tree dependency exceeds the local MVP boundary.

## Consequences

Every future process-using adapter must depend on the command port and construct
explicit policies. Windows `.cmd` and `.bat` candidates are available only when their
extensions are explicitly authorized; Windows may still route batch launchers through
its command processor even though Revanent never sets `shell=True`. An allowed tool
can launch descendants, use the network, or modify accessible files, so this is policy
control rather than operating-system isolation.

Resolved-path checks reduce traversal, link, junction, and current-directory attacks
but cannot eliminate a malicious concurrent filesystem replacement between validation
and use. Stable artifact names assume correlation identifiers are unique within their
owned artifact directory. Redaction guarantees configured exact values and supported
patterns, not universal secret discovery.

## Status

Accepted - 2026-07-30; implemented and verified in P2-001.
