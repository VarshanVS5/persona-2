"""Speech providers: gated live path, working mocks, guaranteed text fallback.

OWNER: Person 2 - Voice, Intent & 9Router.

Master PRD FR-01 and FR-13. The live AWS calls are not implemented this sprint
(``INSTRUCTIONS.md`` section 6 forbids a live AWS call before Person 4 confirms
region/access/budget, and ``boto3`` is not a declared dependency). These tests
assert that the gate is real - that no code path contacts AWS - rather than
asserting that transcription works.
"""

from __future__ import annotations

import pytest

from atlas.intent.voice import VoiceIntake
from atlas.providers.aws_polly import (
    MAX_SYNTHESIS_CHARS,
    AwsPollyProvider,
    MockPollyProvider,
    build_text_to_speech,
)
from atlas.providers.aws_transcribe import (
    MAX_AUDIO_BYTES,
    AwsTranscribeProvider,
    MockTranscribeProvider,
    build_speech_to_text,
)
from atlas.providers.base import ProviderErrorCode
from atlas.providers.settings import ProviderSettings


# -- the gate is real -----------------------------------------------------


def test_transcribe_reports_that_the_live_call_is_not_implemented(mock_settings):
    provider = AwsTranscribeProvider(mock_settings)

    assert provider.available is False
    assert provider.live_call_implemented is False


def test_transcribe_refuses_even_when_fully_flagged_on():
    """Flags on + region set must still not fabricate a working integration."""
    settings = ProviderSettings(
        mock_mode=False, aws_transcribe_enabled=True, aws_region="ap-south-1"
    )
    provider = AwsTranscribeProvider(settings)
    result = provider.transcribe(b"audio", sample_rate_hz=16_000, language_code="en-US")

    assert not result.ok
    assert result.error.code is ProviderErrorCode.DISABLED
    assert "not implemented" in result.error.message


def test_transcribe_unconfigured_reports_not_configured(mock_settings):
    result = AwsTranscribeProvider(mock_settings).transcribe(b"audio")

    assert result.error.code is ProviderErrorCode.NOT_CONFIGURED
    assert "AWS_TRANSCRIBE_ENABLED is false" in result.error.message


def test_transcribe_errors_point_at_the_text_fallback(mock_settings):
    result = AwsTranscribeProvider(mock_settings).transcribe(b"audio")
    assert result.error.details.get("fallback") == "text-input"


def test_transcribe_never_imports_boto3(mock_settings):
    """A stub that quietly dialled AWS would be worse than no stub."""
    import sys

    sys.modules.pop("boto3", None)
    AwsTranscribeProvider(mock_settings).transcribe(b"audio")

    assert "boto3" not in sys.modules


def test_polly_reports_that_the_live_call_is_not_implemented(mock_settings):
    provider = AwsPollyProvider(mock_settings)

    assert provider.available is False
    assert provider.live_call_implemented is False


def test_polly_refuses_even_when_fully_flagged_on():
    settings = ProviderSettings(mock_mode=False, aws_polly_enabled=True, aws_region="ap-south-1")
    result = AwsPollyProvider(settings).synthesize("hello")

    assert not result.ok
    assert result.error.code is ProviderErrorCode.DISABLED


# -- input bounds enforced before any upload would happen -----------------


def test_empty_audio_is_rejected(mock_settings):
    result = AwsTranscribeProvider(mock_settings).transcribe(b"")
    assert result.error.code is ProviderErrorCode.BAD_REQUEST


def test_oversized_audio_is_rejected(mock_settings):
    result = AwsTranscribeProvider(mock_settings).transcribe(b"x" * (MAX_AUDIO_BYTES + 1))

    assert result.error.code is ProviderErrorCode.BAD_REQUEST
    assert "ceiling" in result.error.message


def test_oversized_synthesis_text_is_rejected(mock_settings):
    """Guards against narrating untrusted document or clipboard content."""
    result = AwsPollyProvider(mock_settings).synthesize("x" * (MAX_SYNTHESIS_CHARS + 1))

    assert result.error.code is ProviderErrorCode.BAD_REQUEST


@pytest.mark.parametrize("text", ["", "   "])
def test_empty_synthesis_text_is_rejected(mock_settings, text):
    assert AwsPollyProvider(mock_settings).synthesize(text).error.code is ProviderErrorCode.BAD_REQUEST


# -- mocks ----------------------------------------------------------------


def test_mock_transcriber_returns_labelled_results():
    result = MockTranscribeProvider(["open my editor"]).transcribe(b"audio")

    assert result.ok
    assert result.unwrap() == "open my editor"
    assert result.mocked is True


def test_mock_transcriber_scripts_a_sequence():
    provider = MockTranscribeProvider(["first", "second"])

    assert provider.transcribe(b"a").unwrap() == "first"
    assert provider.transcribe(b"a").unwrap() == "second"
    assert provider.transcribe(b"a").unwrap() == "second", "last entry repeats"


def test_mock_transcriber_rejects_empty_audio():
    assert not MockTranscribeProvider().transcribe(b"").ok


def test_mock_transcriber_does_not_retain_audio():
    """Privacy: only the byte length is recorded (Master PRD FR-14)."""
    provider = MockTranscribeProvider()
    provider.transcribe(b"sensitive-audio-bytes")

    assert provider.calls == [len(b"sensitive-audio-bytes")]


def test_mock_polly_payload_is_obviously_fake():
    """Playing it must fail loudly rather than appear to work."""
    result = MockPollyProvider().synthesize("Opening Notepad.")

    assert result.ok
    assert result.unwrap().startswith(b"ATLAS-MOCK-AUDIO:")
    assert result.mocked is True


# -- selection ------------------------------------------------------------


def test_mock_mode_selects_the_mock_providers(mock_settings):
    stt, stt_live = build_speech_to_text(mock_settings)
    tts, tts_live = build_text_to_speech(mock_settings)

    assert (stt_live, tts_live) == (False, False)
    assert isinstance(stt, MockTranscribeProvider)
    assert isinstance(tts, MockPollyProvider)


def test_disabled_flags_select_mocks_even_outside_mock_mode():
    settings = ProviderSettings(mock_mode=False, aws_region="ap-south-1")
    stt, is_live = build_speech_to_text(settings)

    assert is_live is False
    assert isinstance(stt, MockTranscribeProvider)


def test_missing_region_selects_mocks_even_when_enabled():
    settings = ProviderSettings(mock_mode=False, aws_transcribe_enabled=True, aws_region="")
    stt, is_live = build_speech_to_text(settings)

    assert is_live is False
    assert isinstance(stt, MockTranscribeProvider)


def test_fully_enabled_selects_the_gated_live_object():
    settings = ProviderSettings(
        mock_mode=False, aws_transcribe_enabled=True, aws_region="ap-south-1"
    )
    stt, is_live = build_speech_to_text(settings)

    assert is_live is True
    assert isinstance(stt, AwsTranscribeProvider)
    # ...and even then it reports itself unavailable, because it is.
    assert stt.available is False


# -- VoiceIntake wrapper --------------------------------------------------


def test_voice_intake_normalizes_the_transcript():
    outcome = VoiceIntake(MockTranscribeProvider(["  open   my  editor "])).transcribe(b"a")

    assert outcome.ok
    assert outcome.request.text == "open my editor"
    assert outcome.request.source == "voice"


def test_voice_intake_falls_back_when_unavailable(mock_settings):
    outcome = VoiceIntake(AwsTranscribeProvider(mock_settings)).transcribe(b"a")

    assert not outcome.ok
    assert outcome.fallback_to_text is True
    assert outcome.error is not None
    assert "type your request" in outcome.user_message().lower()


def test_voice_intake_handles_an_empty_transcript():
    outcome = VoiceIntake(MockTranscribeProvider([""])).transcribe(b"a")

    assert not outcome.ok
    assert outcome.fallback_to_text is True


def test_voice_intake_never_propagates_a_provider_exception():
    """An exception here would turn "voice unavailable" into "task failed"."""

    class Exploding:
        name = "aws-transcribe"
        available = True

        def transcribe(self, audio, *, sample_rate_hz, language_code):
            raise RuntimeError("driver crashed")

    outcome = VoiceIntake(Exploding()).transcribe(b"a")

    assert not outcome.ok
    assert outcome.fallback_to_text is True
    assert outcome.error.code is ProviderErrorCode.MALFORMED_RESPONSE


@pytest.mark.parametrize(
    "code,fragment",
    [
        (ProviderErrorCode.TIMEOUT, "timed out"),
        (ProviderErrorCode.NETWORK, "could not be reached"),
        (ProviderErrorCode.AUTH, "credentials"),
        (ProviderErrorCode.DISABLED, "not available"),
        (ProviderErrorCode.UPSTREAM, "failed"),
    ],
)
def test_user_messages_are_specific_and_actionable(code, fragment):
    from atlas.intent.voice import VoiceOutcome
    from atlas.providers.base import ProviderError

    outcome = VoiceOutcome(
        request=None,
        fallback_to_text=True,
        error=ProviderError(provider="aws-transcribe", code=code),
    )
    message = outcome.user_message().lower()

    assert fragment in message
    assert "type" in message
