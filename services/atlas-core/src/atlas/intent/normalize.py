"""Input normalization - the first thing every user request passes through.

OWNER: Person 2 - Voice, Intent & 9Router.

Both entry points (typed text and a final voice transcript) land here before
anything else happens. The job is narrow and defensive:

1. **Bound it.** Reject oversized input instead of forwarding it to a paid
   provider (Master PRD 9.3, budget control).
2. **Clean it.** Strip control characters and zero-width/bidirectional
   formatting characters, normalise Unicode (NFKC) and whitespace. Invisible
   characters are a real injection vector: text that reads as "open my editor"
   on screen can carry hidden directives.
3. **Strip secrets.** Master PRD 8.3: "Secrets are never passed to the LLM."
   If the user pastes a key or a password, the redacted form is what goes to
   the provider *and* what is stored in the plan, so it also cannot reach an
   event payload or Person 1's journal.
4. **Flag, do not trust.** Prompt-injection markers ("ignore previous
   instructions", "you are now...") are recorded on the result for logging and
   for the UI, but are **not** relied upon as a defence. The actual defence is
   structural: the request is delivered to the model as a JSON string value in
   a separate message, and whatever comes back is schema- and
   registry-validated before it can become an action. Marker lists are
   trivially bypassed; validation is not.
5. **Refuse to guess.** Empty or content-free input produces
   ``needs_clarification`` rather than a speculative plan (Master PRD FR-04:
   "ask a question or halt rather than guess").
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from ..providers.redaction import redact

#: Ceiling on a single request, matching ``validation.MAX_USER_REQUEST_CHARS``.
MAX_REQUEST_CHARS = 2_000

#: Below this many characters there is no plausible desktop command.
MIN_REQUEST_CHARS = 2

#: Characters removed outright: C0/C1 controls (except the whitespace we fold),
#: zero-width and word-joiner characters, and bidirectional overrides.
_STRIP_CHARS = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f"
    r"\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]"
)

_WHITESPACE = re.compile(r"\s+")

#: Known prompt-injection phrasings. Used for *flagging only* - see the module
#: docstring. Never used to silently alter the request.
_INJECTION_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ignore-instructions", re.compile(r"(?i)\bignore\s+(all\s+|any\s+)?(previous|prior|above)\b")),
    ("disregard-instructions", re.compile(r"(?i)\bdisregard\s+(all\s+|the\s+)?(previous|prior|above)\b")),
    ("role-override", re.compile(r"(?i)\byou\s+are\s+now\b|\bact\s+as\s+(?:a\s+)?(?:system|admin|root)\b")),
    ("system-prompt-probe", re.compile(r"(?i)\b(system\s+prompt|your\s+instructions)\b")),
    ("policy-override", re.compile(r"(?i)\b(bypass|override|disable)\s+(the\s+)?(policy|policies|safety|validation|allowlist|confirmation)\b")),
    ("tier-override", re.compile(r"(?i)\b(set|mark|treat)\b.{0,20}\b(risk\s*tier|t0|t1|t2|t3)\b")),
    ("confirmation-waiver", re.compile(r"(?i)\b(no|skip|without)\s+(confirmation|approval|consent)\b")),
    ("fenced-directive", re.compile(r"(?i)^\s*(system|assistant)\s*:", re.MULTILINE)),
)


@dataclass(frozen=True)
class NormalizedRequest:
    """Result of normalizing one user request.

    ``text`` is the value used everywhere downstream - prompt, plan
    ``userRequest``, and events - so a secret removed here cannot reappear
    later. ``original_length`` is kept for diagnostics; the original text is
    deliberately not retained.
    """

    text: str
    source: str
    original_length: int
    needs_clarification: bool = False
    clarification_question: str = ""
    truncated: bool = False
    secrets_redacted: bool = False
    injection_markers: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.needs_clarification

    @property
    def suspicious(self) -> bool:
        """True when the request carried injection-style phrasing."""
        return bool(self.injection_markers)

    def describe(self) -> str:
        """Log-safe one-liner. Never includes the request text itself.

        The text is already redacted, but a user request can still contain
        private content (Master PRD FR-14), so the default log view reports
        shape rather than content.
        """
        parts = [
            f"source={self.source}",
            f"chars={len(self.text)}",
            f"originalChars={self.original_length}",
        ]
        if self.truncated:
            parts.append("truncated=true")
        if self.secrets_redacted:
            parts.append("secretsRedacted=true")
        if self.injection_markers:
            parts.append("injectionMarkers=" + ",".join(self.injection_markers))
        if self.needs_clarification:
            parts.append("needsClarification=true")
        return " ".join(parts)


def detect_injection_markers(text: str) -> tuple[str, ...]:
    """Return the names of injection-style patterns present in ``text``."""
    return tuple(name for name, pattern in _INJECTION_MARKERS if pattern.search(text))


def normalize_request(raw: str | None, *, source: str = "text") -> NormalizedRequest:
    """Normalize one user request from text input or a final voice transcript.

    ``source`` must be ``"text"`` or ``"voice"`` to match
    ``TranscriptPayload.source`` in ``packages/contracts/events.schema.json``.
    """
    if source not in ("text", "voice"):
        raise ValueError(f"source must be 'text' or 'voice', got {source!r}")

    original = raw or ""
    original_length = len(original)

    if not original.strip():
        return NormalizedRequest(
            text="",
            source=source,
            original_length=original_length,
            needs_clarification=True,
            clarification_question=(
                "I did not catch a request. What would you like me to do?"
                if source == "voice"
                else "Please type what you would like me to do."
            ),
        )

    # NFKC folds compatibility forms so visually identical strings normalise to
    # the same bytes; it does not make the text trustworthy, only comparable.
    cleaned = unicodedata.normalize("NFKC", original)
    cleaned = _STRIP_CHARS.sub("", cleaned)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()

    redacted = redact(cleaned)
    secrets_redacted = redacted != cleaned
    cleaned = redacted

    truncated = False
    if len(cleaned) > MAX_REQUEST_CHARS:
        # Truncate rather than reject: the leading part of a long paste is
        # usually the actual command. The event reports truncation, so the user
        # is never misled about what was considered.
        cleaned = cleaned[:MAX_REQUEST_CHARS].rstrip()
        truncated = True

    markers = detect_injection_markers(cleaned)

    if len(cleaned) < MIN_REQUEST_CHARS:
        return NormalizedRequest(
            text=cleaned,
            source=source,
            original_length=original_length,
            needs_clarification=True,
            clarification_question="That request was too short to act on. What would you like me to do?",
            truncated=truncated,
            secrets_redacted=secrets_redacted,
            injection_markers=markers,
        )

    # Input that is only punctuation/symbols carries no intent.
    if not any(ch.isalnum() for ch in cleaned):
        return NormalizedRequest(
            text=cleaned,
            source=source,
            original_length=original_length,
            needs_clarification=True,
            clarification_question="I could not read a request in that input. What would you like me to do?",
            truncated=truncated,
            secrets_redacted=secrets_redacted,
            injection_markers=markers,
        )

    return NormalizedRequest(
        text=cleaned,
        source=source,
        original_length=original_length,
        truncated=truncated,
        secrets_redacted=secrets_redacted,
        injection_markers=markers,
    )
