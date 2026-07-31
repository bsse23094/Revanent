"""Central bounded redaction for command results, errors, and artifacts."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

REDACTION_MARKER = "[REDACTED]"
MAX_REDACTION_SECRETS = 128
MAX_REDACTION_SECRET_LENGTH = 8 * 1_024
MAX_REDACTION_SECRET_BYTES = 256 * 1_024

_AUTHORIZATION = re.compile(r"(?i)(\bauthorization\s*:\s*(?:bearer|basic)\s+)([^\s,;]+)")
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)((?:[\"'])?\b(?:api[_-]?key|access[_-]?key|client[_-]?secret|credential|"
    r"password|passwd|private[_-]?key|secret|token)\b(?:[\"'])?\s*[:=]\s*)"
    r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;]+)"
)
_TOKEN_URL_PARAMETER = re.compile(
    r"(?i)([?&](?:access[_-]?token|api[_-]?key|auth|credential|password|secret|token)=)"
    r"([^&#\s]+)"
)


@dataclass(frozen=True, slots=True)
class Redactor:
    """Deterministically remove configured values and common credential forms."""

    secrets: tuple[str, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        if any(not isinstance(secret, str) for secret in self.secrets):
            raise ValueError("redaction secrets must be strings")
        unique = {secret for secret in self.secrets if secret}
        if len(unique) > MAX_REDACTION_SECRETS:
            raise ValueError(f"redaction is limited to {MAX_REDACTION_SECRETS} secrets")
        if any(len(secret) > MAX_REDACTION_SECRET_LENGTH for secret in unique):
            raise ValueError(
                f"redaction secrets are limited to {MAX_REDACTION_SECRET_LENGTH} characters"
            )
        if sum(len(secret.encode("utf-8")) for secret in unique) > MAX_REDACTION_SECRET_BYTES:
            raise ValueError(
                f"redaction secret data is limited to {MAX_REDACTION_SECRET_BYTES} bytes"
            )
        object.__setattr__(
            self, "secrets", tuple(sorted(unique, key=lambda item: (-len(item), item)))
        )

    def with_secrets(self, secrets: tuple[str, ...]) -> Redactor:
        return Redactor((*self.secrets, *secrets))

    def redact(self, value: str, *, truncated: bool = False) -> str:
        """Redact exact values; protect configured-secret prefixes at truncation edges."""
        redacted = value
        for secret in self.secrets:
            redacted = redacted.replace(secret, REDACTION_MARKER)
        redacted = _AUTHORIZATION.sub(lambda match: f"{match.group(1)}{REDACTION_MARKER}", redacted)
        redacted = _CREDENTIAL_ASSIGNMENT.sub(
            lambda match: f"{match.group(1)}{REDACTION_MARKER}", redacted
        )
        redacted = _TOKEN_URL_PARAMETER.sub(
            lambda match: f"{match.group(1)}{REDACTION_MARKER}", redacted
        )
        if truncated:
            longest_prefix = 0
            for secret in self.secrets:
                maximum = min(len(secret) - 1, len(redacted))
                for length in range(maximum, 0, -1):
                    if redacted.endswith(secret[:length]):
                        longest_prefix = max(longest_prefix, length)
                        break
            if longest_prefix:
                redacted = f"{redacted[:-longest_prefix]}{REDACTION_MARKER}"
        return redacted

    def decode_and_redact(self, value: bytes, *, truncated: bool = False) -> str:
        decoded = value.decode("utf-8", errors="replace")
        return self.redact(decoded, truncated=truncated)
