"""Text-to-speech provider interface (Amazon Polly) - gated and mockable.

OWNER: Person 2 - Voice, Intent & 9Router.

Same honest status as :mod:`atlas.providers.aws_transcribe`: the interface and
the offline mock are implemented and tested; the **live Amazon Polly call is
not implemented** and :class:`AwsPollyProvider` contains no code path that
contacts AWS. ``AWS_POLLY_ENABLED`` defaults to ``false``, and spoken output is
an optional convenience, never a channel that reports success. Verified status
always reaches the user through events and the UI (Master PRD 10.2-10.3).

Privacy note (Master PRD FR-14): only text this backend itself composed is
eligible for synthesis. :meth:`AwsPollyProvider.synthesize` and the mock both
refuse oversized text, and callers must not pass document, clipboard, or
screen contents.
"""

from __future__ import annotations

import logging

from .base import (
    PROVIDER_AWS_POLLY,
    ProviderError,
    ProviderErrorCode,
    ProviderResult,
)
from .settings import ProviderSettings

logger = logging.getLogger("atlas.providers.aws_polly")

#: Spoken feedback is one or two short sentences; anything larger signals that
#: a caller is about to narrate untrusted content.
MAX_SYNTHESIS_CHARS = 600
DEFAULT_VOICE_ID = "Joanna"


class AwsPollyProvider:
    """Gate in front of Amazon Polly. Performs no network call."""

    name = PROVIDER_AWS_POLLY
    is_mock = False
    live_call_implemented = False

    def __init__(self, settings: ProviderSettings) -> None:
        self._settings = settings

    @property
    def available(self) -> bool:
        return False

    @property
    def unavailable_reason(self) -> str:
        if not self._settings.aws_polly_enabled:
            return "AWS_POLLY_ENABLED is false"
        if not self._settings.aws_region:
            return "AWS_REGION is not set"
        return (
            "Live Amazon Polly is not implemented in this sprint "
            "(boto3 is not a declared dependency; Person 4 has not confirmed "
            "region/access/budget)"
        )

    def synthesize(self, text: str, *, voice_id: str = DEFAULT_VOICE_ID) -> ProviderResult[bytes]:
        if not text or not text.strip():
            return ProviderResult.failure(
                ProviderError(
                    provider=self.name,
                    code=ProviderErrorCode.BAD_REQUEST,
                    message="Empty synthesis text",
                    retryable=False,
                )
            )
        if len(text) > MAX_SYNTHESIS_CHARS:
            return ProviderResult.failure(
                ProviderError(
                    provider=self.name,
                    code=ProviderErrorCode.BAD_REQUEST,
                    message=f"Text of {len(text)} chars exceeds the {MAX_SYNTHESIS_CHARS}-char ceiling",
                    retryable=False,
                )
            )

        code = (
            ProviderErrorCode.NOT_CONFIGURED
            if not (self._settings.aws_polly_enabled and self._settings.aws_region)
            else ProviderErrorCode.DISABLED
        )
        logger.info("Polly request refused locally: %s", self.unavailable_reason)
        return ProviderResult.failure(
            ProviderError(
                provider=self.name,
                code=code,
                message=self.unavailable_reason,
                retryable=False,
                details={"fallback": "silent-ui-only"},
            )
        )


class MockPollyProvider:
    """Offline stand-in that returns a deterministic, clearly fake payload."""

    name = PROVIDER_AWS_POLLY
    is_mock = True
    live_call_implemented = False

    #: Not valid audio and not meant to be: it is a labelled marker so that a
    #: caller which tries to play it fails loudly instead of appearing to work.
    MOCK_PAYLOAD_PREFIX = b"ATLAS-MOCK-AUDIO:"

    def __init__(self) -> None:
        self.calls: list[str] = []

    @property
    def available(self) -> bool:
        return True

    @property
    def unavailable_reason(self) -> str:
        return ""

    def synthesize(self, text: str, *, voice_id: str = DEFAULT_VOICE_ID) -> ProviderResult[bytes]:
        self.calls.append(text)
        if not text or not text.strip():
            return ProviderResult.failure(
                ProviderError(
                    provider=self.name,
                    code=ProviderErrorCode.BAD_REQUEST,
                    message="Empty synthesis text",
                    retryable=False,
                ),
                mocked=True,
            )
        payload = self.MOCK_PAYLOAD_PREFIX + text.strip()[:MAX_SYNTHESIS_CHARS].encode("utf-8")
        return ProviderResult.success(payload, mocked=True)


def build_text_to_speech(settings: ProviderSettings) -> tuple[object, bool]:
    """Return ``(provider, is_live)``. See :func:`aws_transcribe.build_speech_to_text`."""
    if not settings.mock_mode and settings.aws_polly_enabled and settings.aws_region:
        return AwsPollyProvider(settings), True
    return MockPollyProvider(), False
