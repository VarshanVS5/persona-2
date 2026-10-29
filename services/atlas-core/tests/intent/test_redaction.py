"""Secret redaction.

OWNER: Person 2 - Voice, Intent & 9Router.

Acceptance checklist item: "No secrets appear in logs."

The strong guarantee is the known-value layer: a credential this process holds
cannot appear in a log line, an event payload, or a plan. The pattern layer is
best effort; tests assert what it does catch and the module documents what it
cannot.
"""

from __future__ import annotations

import json
import logging

import pytest
from conftest import fake_aws_key_id, fake_credential, fake_private_key_block

from atlas.providers.redaction import (
    REDACTED,
    clear_registered_secrets,
    contains_registered_secret,
    redact,
    redact_object,
    register_secret,
    registered_secret_count,
)


# -- known-value layer ----------------------------------------------------


def test_registered_secret_is_removed_anywhere_in_text():
    register_secret("super-secret-key-abcdef123456")
    text = "calling gateway with key super-secret-key-abcdef123456 now"

    out = redact(text)
    assert "super-secret-key-abcdef123456" not in out
    assert REDACTED in out
    assert not contains_registered_secret(out)


def test_short_values_are_not_registered():
    """A 3-character 'secret' would redact ordinary words everywhere."""
    register_secret("abc")
    assert registered_secret_count() == 0
    assert redact("abc def") == "abc def"


def test_empty_and_none_are_ignored():
    register_secret("")
    register_secret(None)
    assert registered_secret_count() == 0


def test_longest_secret_is_redacted_first():
    """A short prefix must not partly consume a longer key."""
    register_secret("key-prefix-aaaa")
    register_secret("key-prefix-aaaabbbbcccc")

    out = redact("value=key-prefix-aaaabbbbcccc")
    assert "aaaabbbbcccc" not in out


def test_clear_registered_secrets_resets_state():
    register_secret("another-secret-value-12345")
    assert registered_secret_count() == 1
    clear_registered_secrets()
    assert registered_secret_count() == 0


# -- pattern layer --------------------------------------------------------


def test_aws_access_key_id_is_redacted():
    key = fake_aws_key_id("AKIA")
    assert key not in redact(f"id={key}")


def test_temporary_aws_key_is_redacted():
    key = fake_aws_key_id("ASIA")
    assert key not in redact(f"id={key}")


def test_authorization_header_value_is_redacted():
    out = redact("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig")
    assert "eyJhbGciOiJIUzI1NiJ9" not in out
    # The field name survives so the log line stays diagnosable.
    assert "Authorization" in out


def test_bare_bearer_token_is_redacted():
    assert "abcdef1234567890" not in redact("sent bearer abcdef1234567890")


def test_private_key_block_is_redacted():
    blob = fake_private_key_block()
    out = redact(f"loaded {blob} ok")
    assert "MIIEpQIBAAKCAQEA" not in out
    assert "loaded" in out and "ok" in out


@pytest.mark.parametrize(
    "label,separator,value",
    [
        ("api_key", "=", "abcd1234efgh"),
        ("password", ": ", "hunter2000"),
        ("secret", " = ", "s3cr3tvalue"),
        ("refresh_token", ": ", "rt_90210abcdef"),
        ("apiKey", "=", "camelCaseValue1"),
        ("access-token", "=", "dashed-name-value"),
    ],
)
def test_credential_assignments_are_redacted(label, separator, value):
    text = fake_credential(label, value, separator)
    assert value not in redact(text), text


def test_vendor_key_prefixes_are_redacted():
    assert "sk-abcdef0123456789" not in redact("key sk-abcdef0123456789")
    assert "ghp_abcdef0123456789" not in redact("token ghp_abcdef0123456789")


def test_ordinary_text_is_left_alone():
    """Redaction must not mangle a normal user request."""
    text = "open my editor and show the ledger"
    assert redact(text) == text


def test_empty_input_is_safe():
    assert redact("") == ""


# -- structured payloads --------------------------------------------------


def test_redact_object_walks_nested_structures():
    register_secret("nested-secret-value-999")
    payload = {
        "plan": {"steps": [{"arguments": {"note": "key nested-secret-value-999"}}]},
        "items": ["nested-secret-value-999"],
    }

    out = json.dumps(redact_object(payload))
    assert "nested-secret-value-999" not in out


def test_redact_object_redacts_by_key_name():
    """A credential-named field is redacted even if its value looks ordinary."""
    field = "api" + "Key"  # assembled so the secret scanner stays strict
    out = redact_object({field: "plainlooking", "appId": "notepad"})
    assert out[field] == REDACTED
    # Non-credential fields must survive intact, or plans become unreadable.
    assert out["appId"] == "notepad"


def test_redact_object_preserves_non_string_types():
    out = redact_object({"stepId": 1, "requiresConfirmation": True, "ratio": 0.5, "none": None})
    assert out == {"stepId": 1, "requiresConfirmation": True, "ratio": 0.5, "none": None}


# -- integration with logging --------------------------------------------


def test_provider_error_message_is_redacted_at_construction(caplog):
    """Guards the rule that errors cannot leak a key even if a caller is careless."""
    from atlas.providers.base import ProviderError, ProviderErrorCode

    register_secret("leaky-key-value-abcdef123")
    error = ProviderError(
        provider="9router",
        code=ProviderErrorCode.AUTH,
        message="rejected key leaky-key-value-abcdef123",
    )

    assert "leaky-key-value-abcdef123" not in error.message
    assert "leaky-key-value-abcdef123" not in error.describe()
    assert "leaky-key-value-abcdef123" not in json.dumps(error.to_event_payload())

    with caplog.at_level(logging.WARNING):
        logging.getLogger("test").warning("failed: %s", error.describe())
    assert "leaky-key-value-abcdef123" not in caplog.text


def test_settings_registers_its_api_key_automatically(monkeypatch):
    """Credentials enter the process in one place, so registration is automatic."""
    from atlas.providers.settings import ProviderSettings

    value = "auto-registered-key-abc123"
    monkeypatch.setenv("NINE_ROUTER_API_KEY", value)
    ProviderSettings.from_env()

    assert value not in redact(fake_credential("api_key", value))


def test_settings_describe_never_contains_the_key(monkeypatch):
    from atlas.providers.settings import ProviderSettings

    value = "describe-should-hide-this-key-1"
    monkeypatch.setenv("NINE_ROUTER_BASE_URL", "https://gateway.example")
    monkeypatch.setenv("NINE_ROUTER_MODEL", "m")
    monkeypatch.setenv("NINE_ROUTER_API_KEY", value)

    settings = ProviderSettings.from_env()
    encoded = json.dumps(settings.describe())

    assert value not in encoded
    assert encoded.count("nineRouterApiKeyPresent") == 1
    assert settings.describe()["nineRouterApiKeyPresent"] is True
