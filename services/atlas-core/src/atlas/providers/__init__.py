"""OWNER: Person 2 - Voice, Intent & 9Router. Do not edit without owner approval.

9Router client, optional AWS Transcribe/Polly adapters. Backend only; never
expose keys to the UI.

Public surface (Master PRD 10.4 "Provider interface"): every adapter returns a
typed :class:`ProviderResult`, bounds its own timeout and retries, and redacts
its own log and error strings. A provider failure is always a typed error -
never an exception that escapes, and never a silent success.
"""

from .base import (
    PROVIDER_AWS_POLLY,
    PROVIDER_AWS_TRANSCRIBE,
    PROVIDER_NINE_ROUTER,
    RETRYABLE_CODES,
    VALID_PROVIDERS,
    ChatProvider,
    ProviderCallFailed,
    ProviderError,
    ProviderErrorCode,
    ProviderResult,
    SpeechToTextProvider,
    TextToSpeechProvider,
)
from .aws_polly import AwsPollyProvider, MockPollyProvider, build_text_to_speech
from .aws_transcribe import (
    AwsTranscribeProvider,
    MockTranscribeProvider,
    build_speech_to_text,
)
from .nine_router import MockNineRouterClient, NineRouterClient, build_chat_provider
from .redaction import REDACTED, redact, redact_object, register_secret
from .settings import ProviderSettings

__all__ = [
    "PROVIDER_AWS_POLLY",
    "PROVIDER_AWS_TRANSCRIBE",
    "PROVIDER_NINE_ROUTER",
    "REDACTED",
    "RETRYABLE_CODES",
    "VALID_PROVIDERS",
    "AwsPollyProvider",
    "AwsTranscribeProvider",
    "ChatProvider",
    "MockNineRouterClient",
    "MockPollyProvider",
    "MockTranscribeProvider",
    "NineRouterClient",
    "ProviderCallFailed",
    "ProviderError",
    "ProviderErrorCode",
    "ProviderResult",
    "ProviderSettings",
    "SpeechToTextProvider",
    "TextToSpeechProvider",
    "build_chat_provider",
    "build_speech_to_text",
    "build_text_to_speech",
    "redact",
    "redact_object",
    "register_secret",
]
