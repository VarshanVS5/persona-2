"""Provider-scoped settings loaded from the agreed ``.env.example`` keys.

OWNER: Person 2 - Voice, Intent & 9Router.

INTERIM MODULE - see ``reference/person-2-voice-9router/docs/change-requests/``.
``services/atlas-core/src/atlas/config.py`` is owned by the project owner and
is still a placeholder. Rather than edit a file I do not own, this module reads
*only* the provider keys this workstream consumes, and exposes
:func:`ProviderSettings.from_mapping` so the owner's ``config.py`` can supply
the values later without touching any call site:

    ProviderSettings.from_mapping(owner_config_as_dict)

Keys consumed (all already present in the repository ``.env.example``):
``ATLAS_MOCK_MODE``, ``NINE_ROUTER_BASE_URL``, ``NINE_ROUTER_MODEL``,
``NINE_ROUTER_API_KEY``, ``AWS_REGION``, ``AWS_TRANSCRIBE_ENABLED``,
``AWS_POLLY_ENABLED``, ``ATLAS_LOG_LEVEL``.

Keys this module reads that are NOT yet in ``.env.example`` (change request
``20261029-project-owner-nine-router-tuning-keys.md``; every one has a working
default, so nothing breaks until the owner accepts them):
``NINE_ROUTER_CHAT_PATH``, ``NINE_ROUTER_TIMEOUT_MS``,
``NINE_ROUTER_MAX_ATTEMPTS``, ``NINE_ROUTER_MAX_OUTPUT_TOKENS``.

Fail-safe defaults: mock mode is **on** unless explicitly disabled, and both
AWS speech providers are **off** unless explicitly enabled. An absent or
malformed value therefore never produces a live cloud call.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from .redaction import register_secret

#: 9Router is OpenAI-compatible; it exposes POST /v1/chat/completions.
#: Verified against the public 9Router documentation
#: (https://github.com/didin-lab/9router-setup-guide - "OpenAI-compatible
#: proxy (/v1/chat/completions)"). The exact deployment URL for this project is
#: still OPEN in docs/decisions.md (M-18 Q6), hence the override key.
DEFAULT_CHAT_PATH = "/v1/chat/completions"
DEFAULT_TIMEOUT_MS = 20_000
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_MAX_OUTPUT_TOKENS = 1_200

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


def _as_bool(raw: str | None, *, default: bool) -> bool:
    """Parse a flag. An unrecognised value falls back to ``default``.

    Deliberately not strict: a typo in ``.env`` must not crash the backend, and
    the defaults chosen by callers are always the safe direction (mock on,
    cloud off).
    """
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return default


def _as_int(raw: str | None, *, default: int, minimum: int, maximum: int) -> int:
    """Parse and clamp an integer, falling back to ``default`` when unusable."""
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


@dataclass(frozen=True)
class ProviderSettings:
    """Immutable provider configuration snapshot."""

    mock_mode: bool = True
    nine_router_base_url: str = ""
    nine_router_model: str = ""
    nine_router_api_key: str = ""
    nine_router_chat_path: str = DEFAULT_CHAT_PATH
    nine_router_timeout_ms: int = DEFAULT_TIMEOUT_MS
    nine_router_max_attempts: int = DEFAULT_MAX_ATTEMPTS
    nine_router_max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    aws_region: str = ""
    aws_transcribe_enabled: bool = False
    aws_polly_enabled: bool = False
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        # Any credential we hold is registered for exact-substring redaction so
        # it cannot appear in a log line, event, or plan. Done here because this
        # is the single place credentials enter the process.
        register_secret(self.nine_router_api_key)

    @classmethod
    def from_mapping(cls, env: Mapping[str, str]) -> "ProviderSettings":
        return cls(
            mock_mode=_as_bool(env.get("ATLAS_MOCK_MODE"), default=True),
            nine_router_base_url=(env.get("NINE_ROUTER_BASE_URL") or "").strip(),
            nine_router_model=(env.get("NINE_ROUTER_MODEL") or "").strip(),
            # Value deliberately on its own line: `scripts/check-secrets.ps1`
            # flags `api_key=<8+ chars>` on a single source line, and keeping
            # the scanner strict is worth more than one compact line here.
            nine_router_api_key=(
                env.get("NINE_ROUTER_API_KEY") or ""
            ).strip(),
            nine_router_chat_path=(env.get("NINE_ROUTER_CHAT_PATH") or DEFAULT_CHAT_PATH).strip()
            or DEFAULT_CHAT_PATH,
            nine_router_timeout_ms=_as_int(
                env.get("NINE_ROUTER_TIMEOUT_MS"),
                default=DEFAULT_TIMEOUT_MS,
                minimum=1_000,
                maximum=120_000,
            ),
            nine_router_max_attempts=_as_int(
                env.get("NINE_ROUTER_MAX_ATTEMPTS"),
                default=DEFAULT_MAX_ATTEMPTS,
                minimum=1,
                maximum=5,
            ),
            nine_router_max_output_tokens=_as_int(
                env.get("NINE_ROUTER_MAX_OUTPUT_TOKENS"),
                default=DEFAULT_MAX_OUTPUT_TOKENS,
                minimum=256,
                maximum=8_192,
            ),
            aws_region=(env.get("AWS_REGION") or "").strip(),
            aws_transcribe_enabled=_as_bool(env.get("AWS_TRANSCRIBE_ENABLED"), default=False),
            aws_polly_enabled=_as_bool(env.get("AWS_POLLY_ENABLED"), default=False),
            log_level=(env.get("ATLAS_LOG_LEVEL") or "INFO").strip().upper(),
        )

    @classmethod
    def from_env(cls) -> "ProviderSettings":
        return cls.from_mapping(os.environ)

    @property
    def nine_router_configured(self) -> bool:
        """True when a live 9Router call is possible at all."""
        return bool(self.nine_router_base_url and self.nine_router_model and self.nine_router_api_key)

    @property
    def nine_router_url(self) -> str:
        """Absolute chat-completions URL."""
        if not self.nine_router_base_url:
            return ""
        return self.nine_router_base_url.rstrip("/") + "/" + self.nine_router_chat_path.lstrip("/")

    @property
    def use_live_llm(self) -> bool:
        """Live 9Router is used only when mock mode is off *and* it is configured."""
        return not self.mock_mode and self.nine_router_configured

    def describe(self) -> dict[str, object]:
        """Log/diagnostics view. Never includes a secret value.

        ``nine_router_api_key`` is reported only as a boolean presence flag,
        which is what a "is it configured?" diagnostic actually needs.
        """
        return {
            "mockMode": self.mock_mode,
            "nineRouterUrl": self.nine_router_url or "(unset)",
            "nineRouterModel": self.nine_router_model or "(unset)",
            "nineRouterApiKeyPresent": bool(self.nine_router_api_key),
            "nineRouterTimeoutMs": self.nine_router_timeout_ms,
            "nineRouterMaxAttempts": self.nine_router_max_attempts,
            "awsRegion": self.aws_region or "(unset)",
            "awsTranscribeEnabled": self.aws_transcribe_enabled,
            "awsPollyEnabled": self.aws_polly_enabled,
            "useLiveLlm": self.use_live_llm,
        }
