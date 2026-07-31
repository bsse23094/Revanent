import pytest

from revanent.commands import REDACTION_MARKER, Redactor


def test_overlapping_configured_secrets_are_removed_deterministically() -> None:
    redactor = Redactor(("token-abcdef", "abcdef", "token"))

    result = redactor.redact("token-abcdef token abcdef")

    assert result == f"{REDACTION_MARKER} {REDACTION_MARKER} {REDACTION_MARKER}"
    assert "token" not in result.casefold()
    assert "abcdef" not in result


def test_authorization_and_credential_assignments_are_redacted() -> None:
    result = Redactor().redact(
        'Authorization: Bearer abc123 password=hunter2 API_KEY: key-value "access_key": "json-key"'
    )

    assert result == (
        f"Authorization: Bearer {REDACTION_MARKER} "
        f"password={REDACTION_MARKER} API_KEY: {REDACTION_MARKER} "
        f'"access_key": {REDACTION_MARKER}'
    )


def test_truncated_configured_secret_prefix_is_not_revealed() -> None:
    redactor = Redactor(("prefix-and-secret",))

    assert redactor.redact("output prefix-and-", truncated=True) == (f"output {REDACTION_MARKER}")


def test_invalid_utf8_is_replaced_before_redaction() -> None:
    assert Redactor().decode_and_redact(b"valid\xfftail") == "valid\ufffdtail"


def test_secret_configuration_is_hidden_and_bounded() -> None:
    assert "configured-secret" not in repr(Redactor(("configured-secret",)))
    with pytest.raises(ValueError, match="limited"):
        Redactor(tuple(f"secret-{index}" for index in range(129)))
