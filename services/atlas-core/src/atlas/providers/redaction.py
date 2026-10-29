"""Secret redaction for provider logs, prompts, events, and plans.

OWNER: Person 2 - Voice, Intent & 9Router.

Master PRD 8.3 ("Secrets are never passed to the LLM"), 10.4 ("redacted
logging"), 12 and events.schema.json ("Redacted; never contains keys or
secrets") all require that no secret value leaves this process inside a log
line, an event payload, a task plan, or an outbound provider request.

Two independent layers are applied, because neither is sufficient alone:

1. *Known-value redaction* - exact substring replacement of secret values this
   process actually holds (for example ``NINE_ROUTER_API_KEY``). This is the
   reliable layer: it cannot miss a credential whose value we know.
2. *Pattern redaction* - regexes for credential shapes we may not hold
   (AWS access key ids, private key blocks, ``Authorization: Bearer`` headers,
   ``password=...`` assignments). This is best effort and intentionally
   conservative; it reduces exposure but is not a proof of absence.

Limitation (disclosed, not worked around): pattern redaction cannot detect an
arbitrary high-entropy string that carries no recognisable marker. Never rely
on layer 2 alone for a value this process could have registered via
:func:`register_secret`.
"""

from __future__ import annotations

import re
from typing import Iterable

#: Replacement marker written in place of any redacted value.
REDACTED = "[REDACTED]"

#: Shortest secret value that is worth substring-redacting. Short values (for
#: example an empty or placeholder key from ``.env.example``) would otherwise
#: match everywhere and destroy the readability of every log line.
_MIN_SECRET_LENGTH = 8

_KNOWN_SECRETS: set[str] = set()

# Ordered: more specific patterns first. Each pattern keeps its non-secret
# prefix (via a capture group) so redacted text stays diagnosable.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        REDACTED,
    ),
    # AWS access key id / temporary access key id.
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), REDACTED),
    # Authorization headers and bearer tokens.
    (
        re.compile(r"(?i)\b(authorization\s*[:=]\s*)(?:bearer\s+)?\S+"),
        r"\1" + REDACTED,
    ),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]{8,}"), r"\1 " + REDACTED),
    # key=value / "key": "value" assignments for credential-ish names.
    (
        re.compile(
            r"(?i)\b(api[_-]?key|secret[_-]?access[_-]?key|secret[_-]?key|secret"
            r"|access[_-]?token|refresh[_-]?token|token|password|passwd|pwd|mfa"
            r"|otp)\b(\"?\s*[:=]\s*\"?)([^\s\"',;}]{4,})"
        ),
        r"\1\2" + REDACTED,
    ),
    # Common vendor key prefixes (OpenAI-compatible gateways, GitHub, Slack).
    (re.compile(r"\bsk-[A-Za-z0-9._\-]{8,}"), REDACTED),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{8,}"), REDACTED),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{8,}"), REDACTED),
)


def register_secret(value: str | None) -> None:
    """Register a secret value for exact-substring redaction.

    Called by provider settings loading with every credential read from the
    environment. Values shorter than :data:`_MIN_SECRET_LENGTH` are ignored to
    avoid redacting ordinary words; such values are not usable credentials.
    """
    if not value:
        return
    text = value.strip()
    if len(text) < _MIN_SECRET_LENGTH:
        return
    _KNOWN_SECRETS.add(text)


def registered_secret_count() -> int:
    """Number of registered secret values (for diagnostics; never the values)."""
    return len(_KNOWN_SECRETS)


def clear_registered_secrets() -> None:
    """Drop all registered secrets. Intended for test isolation."""
    _KNOWN_SECRETS.clear()


def redact(text: str, extra_secrets: Iterable[str] | None = None) -> str:
    """Return ``text`` with known secret values and credential shapes removed."""
    if not text:
        return text

    result = text
    candidates = set(_KNOWN_SECRETS)
    if extra_secrets:
        candidates.update(s.strip() for s in extra_secrets if s and len(s.strip()) >= _MIN_SECRET_LENGTH)

    # Longest first, so a long key is not partly consumed by a shorter prefix.
    for secret in sorted(candidates, key=len, reverse=True):
        if secret in result:
            result = result.replace(secret, REDACTED)

    for pattern, replacement in _PATTERNS:
        result = pattern.sub(replacement, result)

    return result


def redact_object(value: object) -> object:
    """Recursively redact strings inside dicts, lists, and tuples.

    Dict *keys* are preserved (they are field names, not values) but a value
    whose key names a credential is redacted wholesale even if its shape is
    unremarkable.
    """
    secretish = re.compile(
        r"(?i)(api[_-]?key|secret|token|password|passwd|pwd|credential|authorization|mfa|otp)"
    )

    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        out: dict[object, object] = {}
        for key, item in value.items():
            if isinstance(key, str) and secretish.search(key) and isinstance(item, str) and item:
                out[key] = REDACTED
            else:
                out[key] = redact_object(item)
        return out
    if isinstance(value, (list, tuple)):
        return [redact_object(item) for item in value]
    return value


def contains_registered_secret(text: str) -> bool:
    """True if ``text`` still contains a registered secret value.

    Used by tests and by outbound guards to assert that redaction happened.
    """
    return any(secret in text for secret in _KNOWN_SECRETS)
