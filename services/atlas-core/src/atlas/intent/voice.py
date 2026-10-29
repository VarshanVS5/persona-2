"""Voice input handling - transcript capture with a guaranteed text fallback.

OWNER: Person 2 - Voice, Intent & 9Router.

Master PRD FR-01: "ATLAS shall accept text commands. Voice input may be
provided through Amazon Transcribe when enabled and available. Text mode must
remain usable if microphone access, AWS credentials, network access, or speech
services fail."

The contract that makes this true is implemented here: :meth:`VoiceIntake.
transcribe` never raises and never fails a task. Every unhappy path returns a
:class:`VoiceOutcome` carrying ``fallback_to_text=True`` plus a typed
``provider_error`` payload, so the UI can say "voice is unavailable, type
instead" rather than showing a dead microphone button.

Privacy (Master PRD FR-14): audio is passed straight to the configured
provider and is not written to disk, not logged, and not retained on this
object. Only its byte length is recorded, for diagnostics.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..providers.base import ProviderError, ProviderErrorCode, ProviderResult
from .normalize import NormalizedRequest, normalize_request

logger = logging.getLogger("atlas.intent.voice")


@dataclass(frozen=True)
class VoiceOutcome:
    """Result of one voice-capture attempt."""

    request: NormalizedRequest | None
    fallback_to_text: bool
    error: ProviderError | None = None
    mocked: bool = False

    @property
    def ok(self) -> bool:
        return self.request is not None and self.request.ok

    def provider_error_payload(self) -> dict[str, object] | None:
        """``ProviderErrorPayload`` for a ``provider_error`` event, if any."""
        return self.error.to_event_payload() if self.error is not None else None

    def user_message(self) -> str:
        """Short, actionable sentence for the UI. Never blames the user."""
        if self.ok:
            return ""
        if self.error is None:
            return "I could not make out a request. Please try again or type it."
        if self.error.code in (ProviderErrorCode.NOT_CONFIGURED, ProviderErrorCode.DISABLED):
            return "Voice input is not available in this build. Please type your request."
        if self.error.code == ProviderErrorCode.TIMEOUT:
            return "Speech recognition timed out. Please type your request."
        if self.error.code == ProviderErrorCode.NETWORK:
            return "Speech recognition could not be reached. Please type your request."
        if self.error.code == ProviderErrorCode.AUTH:
            return "Speech recognition rejected the configured credentials. Please type your request."
        return "Speech recognition failed. Please type your request."


class VoiceIntake:
    """Wraps a :class:`SpeechToTextProvider` with normalization and fallback."""

    def __init__(self, provider: object) -> None:
        """``provider`` is any object satisfying ``SpeechToTextProvider``."""
        self._provider = provider

    @property
    def provider_name(self) -> str:
        return getattr(self._provider, "name", "unknown")

    @property
    def is_mock(self) -> bool:
        return bool(getattr(self._provider, "is_mock", False))

    @property
    def available(self) -> bool:
        return bool(getattr(self._provider, "available", False))

    @property
    def unavailable_reason(self) -> str:
        return str(getattr(self._provider, "unavailable_reason", "") or "")

    def transcribe(
        self,
        audio: bytes,
        *,
        sample_rate_hz: int = 16_000,
        language_code: str = "en-US",
    ) -> VoiceOutcome:
        """Transcribe ``audio`` into a normalized request.

        Returns a fallback outcome - never raises - for every failure mode,
        including an unexpected provider exception, because an exception here
        would otherwise turn "voice unavailable" into "task failed".
        """
        if not self.available:
            reason = self.unavailable_reason or "speech provider is unavailable"
            logger.info("Voice intake unavailable: %s", reason)
            return VoiceOutcome(
                request=None,
                fallback_to_text=True,
                error=ProviderError(
                    provider=self.provider_name,
                    code=ProviderErrorCode.NOT_CONFIGURED,
                    message=reason,
                    retryable=False,
                    details={"fallback": "text-input"},
                ),
            )

        try:
            result: ProviderResult[str] = self._provider.transcribe(  # type: ignore[attr-defined]
                audio, sample_rate_hz=sample_rate_hz, language_code=language_code
            )
        except Exception as exc:  # defensive: provider contract says it returns errors
            logger.warning("Speech provider raised instead of returning a result: %s", type(exc).__name__)
            return VoiceOutcome(
                request=None,
                fallback_to_text=True,
                error=ProviderError(
                    provider=self.provider_name,
                    code=ProviderErrorCode.MALFORMED_RESPONSE,
                    message=f"Speech provider raised {type(exc).__name__}",
                    retryable=False,
                ),
            )

        if not result.ok:
            assert result.error is not None
            logger.info("Voice intake failed: %s", result.error.describe())
            return VoiceOutcome(
                request=None,
                fallback_to_text=True,
                error=result.error,
                mocked=result.mocked,
            )

        request = normalize_request(result.unwrap(), source="voice")
        logger.info("Voice intake produced a transcript: %s", request.describe())
        return VoiceOutcome(
            request=request,
            # An empty or unintelligible transcript is not a provider failure,
            # but the user still needs the text box: offer both.
            fallback_to_text=request.needs_clarification,
            mocked=result.mocked,
        )
