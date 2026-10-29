"""Shared provider interface: bounded requests, typed results, redacted errors.

OWNER: Person 2 - Voice, Intent & 9Router.

Master PRD 10.4 "Provider interface": every provider adapter exposes bounded
requests, timeouts, typed success/error results, and redacted logging, and a
provider-specific failure must never bypass policy validation or turn a failed
task into a success.

Design consequences enforced here:

* No adapter raises a bare transport exception at its boundary. Every outcome
  is a :class:`ProviderResult`, so a caller cannot accidentally treat an
  exception path as success.
* ``code`` values are a closed taxonomy (:class:`ProviderErrorCode`) that maps
  directly onto the ``provider_error`` event payload in
  ``packages/contracts/events.schema.json``.
* ``retryable`` is decided by the adapter, not the caller, and a non-retryable
  error is never retried.
* ``message`` is passed through :func:`atlas.providers.redaction.redact`
  before it is stored, so an error string can be surfaced to the UI and the
  journal without leaking a credential.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Generic, Protocol, TypeVar, runtime_checkable

from .redaction import redact

T = TypeVar("T")

#: Provider identifiers permitted by ``ProviderErrorPayload.provider`` in
#: ``packages/contracts/events.schema.json``. Using anything else would emit a
#: schema-invalid event.
PROVIDER_NINE_ROUTER = "9router"
PROVIDER_AWS_TRANSCRIBE = "aws-transcribe"
PROVIDER_AWS_POLLY = "aws-polly"

VALID_PROVIDERS = frozenset({PROVIDER_NINE_ROUTER, PROVIDER_AWS_TRANSCRIBE, PROVIDER_AWS_POLLY})


class ProviderErrorCode(str, Enum):
    """Closed failure taxonomy for provider adapters."""

    #: The provider is switched off by feature flag or is unconfigured.
    NOT_CONFIGURED = "NOT_CONFIGURED"
    #: Deliberately disabled, e.g. live AWS call before Person 4 confirms access.
    DISABLED = "DISABLED"
    #: Request exceeded the bounded timeout.
    TIMEOUT = "TIMEOUT"
    #: DNS/socket/TLS failure; the request may not have reached the provider.
    NETWORK = "NETWORK"
    #: Credentials rejected (HTTP 401/403).
    AUTH = "AUTH"
    #: Provider rate limit (HTTP 429).
    RATE_LIMIT = "RATE_LIMIT"
    #: Provider-side failure (HTTP 5xx).
    UPSTREAM = "UPSTREAM"
    #: Provider replied, but the body was not the agreed shape.
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    #: Provider replied with a client error we must not retry (HTTP 4xx other).
    BAD_REQUEST = "BAD_REQUEST"
    #: Response exceeded the byte ceiling we are willing to read.
    RESPONSE_TOO_LARGE = "RESPONSE_TOO_LARGE"
    #: Retry budget exhausted; the last attempt's code is kept in ``details``.
    RETRIES_EXHAUSTED = "RETRIES_EXHAUSTED"
    #: Caller cancelled before completion.
    CANCELLED = "CANCELLED"


#: Codes an adapter may retry. Anything else is terminal for that attempt.
RETRYABLE_CODES = frozenset(
    {
        ProviderErrorCode.TIMEOUT,
        ProviderErrorCode.NETWORK,
        ProviderErrorCode.RATE_LIMIT,
        ProviderErrorCode.UPSTREAM,
    }
)


@dataclass(frozen=True)
class ProviderError:
    """A typed, redacted provider failure.

    ``message`` is redacted in ``__post_init__`` so that constructing an error
    from an exception string cannot leak a credential, regardless of caller
    discipline.
    """

    provider: str
    code: ProviderErrorCode
    message: str = ""
    retryable: bool | None = None
    attempts: int = 1
    details: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.provider not in VALID_PROVIDERS:
            raise ValueError(
                f"provider must be one of {sorted(VALID_PROVIDERS)} to stay "
                f"schema-valid for events.schema.json, got {self.provider!r}"
            )
        object.__setattr__(self, "message", redact(self.message or ""))
        if self.retryable is None:
            object.__setattr__(self, "retryable", self.code in RETRYABLE_CODES)

    def to_event_payload(self) -> dict[str, object]:
        """Return a ``ProviderErrorPayload`` (events.schema.json ``$defs``)."""
        payload: dict[str, object] = {
            "provider": self.provider,
            "code": self.code.value,
            "retryable": bool(self.retryable),
        }
        if self.message:
            payload["message"] = self.message
        return payload

    def describe(self) -> str:
        """Single-line, log-safe description."""
        suffix = f": {self.message}" if self.message else ""
        return f"{self.provider}/{self.code.value} (attempts={self.attempts}, retryable={self.retryable}){suffix}"


@dataclass(frozen=True)
class ProviderResult(Generic[T]):
    """Typed success/error union. Exactly one of ``value``/``error`` is set."""

    value: T | None = None
    error: ProviderError | None = None
    #: True when the value came from a mock/fixture rather than a live call.
    mocked: bool = False
    #: Wall-clock duration of the call in milliseconds, for diagnostics.
    elapsed_ms: int = 0

    def __post_init__(self) -> None:
        if (self.value is None) == (self.error is None):
            raise ValueError("ProviderResult requires exactly one of value or error")

    @property
    def ok(self) -> bool:
        return self.error is None

    def unwrap(self) -> T:
        """Return the value or raise. Callers in the planning path use ``ok``."""
        if self.error is not None:
            raise ProviderCallFailed(self.error)
        assert self.value is not None  # guaranteed by __post_init__
        return self.value

    @classmethod
    def success(cls, value: T, *, mocked: bool = False, elapsed_ms: int = 0) -> "ProviderResult[T]":
        return cls(value=value, mocked=mocked, elapsed_ms=elapsed_ms)

    @classmethod
    def failure(cls, error: ProviderError, *, mocked: bool = False, elapsed_ms: int = 0) -> "ProviderResult[T]":
        return cls(error=error, mocked=mocked, elapsed_ms=elapsed_ms)


class ProviderCallFailed(RuntimeError):
    """Raised only by :meth:`ProviderResult.unwrap`."""

    def __init__(self, error: ProviderError) -> None:
        super().__init__(error.describe())
        self.error = error


@runtime_checkable
class ChatProvider(Protocol):
    """Minimal chat interface the planner depends on.

    Keeping the planner bound to this Protocol (not to the concrete 9Router
    client) is what lets the LLM planner be tested against a stub without
    network access, and lets mock mode substitute a fixture provider.
    """

    name: str

    def complete(self, system_prompt: str, user_prompt: str) -> ProviderResult[str]:
        """Return the assistant message text, or a typed error."""


@runtime_checkable
class SpeechToTextProvider(Protocol):
    """Speech-provider interface (Master PRD FR-01).

    Text input is always the fallback, so every implementation must be able to
    report ``NOT_CONFIGURED``/``DISABLED`` instead of failing the task.
    """

    name: str

    @property
    def available(self) -> bool:
        """True only when the provider is enabled *and* configured."""

    def transcribe(self, audio: bytes, *, sample_rate_hz: int, language_code: str) -> ProviderResult[str]:
        """Return the final transcript text, or a typed error."""


@runtime_checkable
class TextToSpeechProvider(Protocol):
    """Speech-synthesis interface (optional, Master PRD FR-13)."""

    name: str

    @property
    def available(self) -> bool: ...

    def synthesize(self, text: str, *, voice_id: str) -> ProviderResult[bytes]: ...
