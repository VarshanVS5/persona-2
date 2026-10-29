"""Speech-to-text provider interface (Amazon Transcribe) - gated and mockable.

OWNER: Person 2 - Voice, Intent & 9Router.

State of this module, stated plainly:

* The **interface** (:class:`atlas.providers.base.SpeechToTextProvider`) is
  implemented and tested.
* The **offline implementation** (:class:`MockTranscribeProvider`) is
  implemented and tested, and is what the demo uses.
* The **live Amazon Transcribe call is NOT implemented.**
  :class:`AwsTranscribeProvider` is a real gate, not a stub pretending to
  work: it returns a typed ``DISABLED``/``NOT_CONFIGURED`` error for every
  input, and it has no code path that contacts AWS.

Why it is deliberately left that way for this sprint:

1. ``services/atlas-core/pyproject.toml`` (project-owner owned) declares no
   dependencies, and ``boto3`` is not installed. Adding it is a PR to the
   owner, per ``reference/person-2-voice-9router/INSTRUCTIONS.md`` section 1.
2. ``INSTRUCTIONS.md`` section 6: "No live AWS call before Person 4 confirms
   region/access/budget." That confirmation has not happened, and
   ``docs/decisions.md`` M-18 Q5 (Transcribe enabled in region?) and Q10
   (budget stop threshold) are both still OPEN.
3. ``AWS_TRANSCRIBE_ENABLED`` defaults to ``false`` in ``.env.example``.

Master PRD FR-01 requires text input to stay usable when microphone access,
AWS credentials, network access, or speech services fail. That is satisfied by
returning a typed error that the planning path treats as "fall back to text",
never as a task failure.
"""

from __future__ import annotations

import logging

from .base import (
    PROVIDER_AWS_TRANSCRIBE,
    ProviderError,
    ProviderErrorCode,
    ProviderResult,
)
from .settings import ProviderSettings

logger = logging.getLogger("atlas.providers.aws_transcribe")

#: Audio constraints enforced locally before any upload would occur. Master
#: PRD 12/FR-14: transmit only what is necessary. A 60-second ceiling is ample
#: for a single spoken command and bounds both cost and exposure.
MAX_AUDIO_BYTES = 2_000_000
DEFAULT_SAMPLE_RATE_HZ = 16_000
DEFAULT_LANGUAGE_CODE = "en-US"


class AwsTranscribeProvider:
    """Gate in front of Amazon Transcribe. Performs no network call.

    Kept as a class rather than deleted so that the orchestrator can be wired
    to the final interface now, and enabling the live path later is a change
    inside this one file.
    """

    name = PROVIDER_AWS_TRANSCRIBE
    is_mock = False
    #: Explicit, machine-readable honesty flag for diagnostics and the UI.
    live_call_implemented = False

    def __init__(self, settings: ProviderSettings) -> None:
        self._settings = settings

    @property
    def available(self) -> bool:
        """Always False: the live call is not implemented in this sprint."""
        return False

    @property
    def unavailable_reason(self) -> str:
        if not self._settings.aws_transcribe_enabled:
            return "AWS_TRANSCRIBE_ENABLED is false"
        if not self._settings.aws_region:
            return "AWS_REGION is not set"
        return (
            "Live Amazon Transcribe is not implemented in this sprint "
            "(boto3 is not a declared dependency; Person 4 has not confirmed "
            "region/access/budget)"
        )

    def transcribe(
        self,
        audio: bytes,
        *,
        sample_rate_hz: int = DEFAULT_SAMPLE_RATE_HZ,
        language_code: str = DEFAULT_LANGUAGE_CODE,
    ) -> ProviderResult[str]:
        """Return a typed error. Never contacts AWS.

        Input validation still runs first, so a caller integrating against
        this interface gets the same rejection behaviour it will get once the
        live path exists.
        """
        if not audio:
            return ProviderResult.failure(
                ProviderError(
                    provider=self.name,
                    code=ProviderErrorCode.BAD_REQUEST,
                    message="Empty audio buffer",
                    retryable=False,
                )
            )
        if len(audio) > MAX_AUDIO_BYTES:
            return ProviderResult.failure(
                ProviderError(
                    provider=self.name,
                    code=ProviderErrorCode.BAD_REQUEST,
                    message=f"Audio of {len(audio)} bytes exceeds the {MAX_AUDIO_BYTES}-byte ceiling",
                    retryable=False,
                )
            )

        code = (
            ProviderErrorCode.NOT_CONFIGURED
            if not (self._settings.aws_transcribe_enabled and self._settings.aws_region)
            else ProviderErrorCode.DISABLED
        )
        logger.info("Transcribe request refused locally: %s", self.unavailable_reason)
        return ProviderResult.failure(
            ProviderError(
                provider=self.name,
                code=code,
                message=self.unavailable_reason,
                retryable=False,
                details={"fallback": "text-input"},
            )
        )


class MockTranscribeProvider:
    """Deterministic offline transcriber for mock mode, demos, and tests.

    Clearly labelled: ``is_mock`` is True and every result carries
    ``mocked=True``.
    """

    name = PROVIDER_AWS_TRANSCRIBE
    is_mock = True
    live_call_implemented = False

    def __init__(self, transcripts: list[str] | None = None) -> None:
        """``transcripts`` are returned in order; the last entry repeats."""
        self._transcripts = list(transcripts or ["open my editor"])
        self.calls: list[int] = []

    @property
    def available(self) -> bool:
        return True

    @property
    def unavailable_reason(self) -> str:
        return ""

    def transcribe(
        self,
        audio: bytes,
        *,
        sample_rate_hz: int = DEFAULT_SAMPLE_RATE_HZ,
        language_code: str = DEFAULT_LANGUAGE_CODE,
    ) -> ProviderResult[str]:
        self.calls.append(len(audio))
        if not audio:
            return ProviderResult.failure(
                ProviderError(
                    provider=self.name,
                    code=ProviderErrorCode.BAD_REQUEST,
                    message="Empty audio buffer",
                    retryable=False,
                ),
                mocked=True,
            )
        text = self._transcripts[0] if len(self._transcripts) == 1 else self._transcripts.pop(0)
        return ProviderResult.success(text, mocked=True)


def build_speech_to_text(settings: ProviderSettings) -> tuple[object, bool]:
    """Return ``(provider, is_live)``.

    Live is returned only when mock mode is off and the flag plus region are
    set - and even then the provider itself reports ``available is False``
    because the live call is not implemented. The boolean describes which
    *object* was selected, never that speech recognition works.
    """
    if not settings.mock_mode and settings.aws_transcribe_enabled and settings.aws_region:
        return AwsTranscribeProvider(settings), True
    return MockTranscribeProvider(), False
