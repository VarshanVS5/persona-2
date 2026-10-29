"""9Router chat client - backend only, bounded, retried, redacted.

OWNER: Person 2 - Voice, Intent & 9Router.

Scope rules this module exists to enforce:

* **Backend only.** 9Router is reached from ``services/atlas-core`` and never
  from ``apps/desktop-ui``; the API key is read from the backend environment
  and is not present in the frontend bundle (Master PRD FR-13).
* **Bounded.** Every request has a connect/read timeout, a retry ceiling, a
  request-body ceiling, and a response-body ceiling.
* **Typed.** The public surface returns :class:`ProviderResult`; transport
  exceptions never escape.
* **Redacted.** The ``Authorization`` header is never logged, and every error
  string goes through :mod:`atlas.providers.redaction`.
* **Not an authority.** This module returns *text*. It never parses a plan, and
  it certainly never executes anything. Interpretation and validation are
  :mod:`atlas.planner`'s job, which is what keeps untrusted model output on the
  far side of the policy boundary (Master PRD 8.3).

Transport is :mod:`urllib.request` from the standard library, deliberately:
``services/atlas-core/pyproject.toml`` declares no dependencies and is owned by
the project owner, so this workstream must not require a new package to run.

9Router speaks the OpenAI-compatible ``POST /v1/chat/completions`` shape
(public 9Router setup guide: "OpenAI-compatible proxy (/v1/chat/completions)").
The exact deployment URL, model name, and key for this project are still OPEN
in ``docs/decisions.md`` (M-18 Q2/Q6), so **no live call has been verified**;
the request/response shape below is implemented to that documented contract and
is exercised by tests against a local stub HTTP server, not against 9Router.
"""

from __future__ import annotations

import json
import logging
import random
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

from .base import (
    PROVIDER_NINE_ROUTER,
    ProviderError,
    ProviderErrorCode,
    ProviderResult,
)
from .redaction import redact
from .settings import ProviderSettings

logger = logging.getLogger("atlas.providers.nine_router")

#: Refuse to read more than this many bytes of response body. A hostile or
#: broken gateway must not be able to exhaust memory.
MAX_RESPONSE_BYTES = 1_000_000

#: Refuse to send a prompt larger than this. Protects the token budget
#: (Master PRD 9.3) and bounds the blast radius of a pathological input.
MAX_REQUEST_BYTES = 200_000

#: Base backoff delay; actual delay is base * 2**attempt plus jitter.
RETRY_BASE_DELAY_S = 0.25
RETRY_MAX_DELAY_S = 4.0


@dataclass(frozen=True)
class ChatUsage:
    """Token accounting reported by the gateway, when it reports any."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class NineRouterClient:
    """Live 9Router client. Satisfies :class:`atlas.providers.base.ChatProvider`."""

    name = PROVIDER_NINE_ROUTER

    def __init__(
        self,
        settings: ProviderSettings,
        *,
        opener: Callable[[urllib.request.Request, float], object] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """
        ``opener`` and ``sleep`` are injection points for tests: they let the
        retry/timeout/backoff logic be exercised deterministically without a
        network and without real delays.
        """
        self._settings = settings
        self._sleep = sleep
        self._opener = opener or self._default_opener
        self.last_usage: ChatUsage | None = None

    @staticmethod
    def _default_opener(request: urllib.request.Request, timeout: float) -> object:
        # No proxy handler: this must not be silently routed through a system
        # proxy that could log the prompt.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return opener.open(request, timeout=timeout)

    # -- public surface ---------------------------------------------------

    def complete(self, system_prompt: str, user_prompt: str) -> ProviderResult[str]:
        """Send one chat completion and return the assistant text.

        Retries only the codes in
        :data:`atlas.providers.base.RETRYABLE_CODES`, at most
        ``NINE_ROUTER_MAX_ATTEMPTS`` times in total.
        """
        settings = self._settings
        if not settings.nine_router_configured:
            missing = [
                key
                for key, value in (
                    ("NINE_ROUTER_BASE_URL", settings.nine_router_base_url),
                    ("NINE_ROUTER_MODEL", settings.nine_router_model),
                    ("NINE_ROUTER_API_KEY", settings.nine_router_api_key),
                )
                if not value
            ]
            return ProviderResult.failure(
                ProviderError(
                    provider=self.name,
                    code=ProviderErrorCode.NOT_CONFIGURED,
                    message=f"9Router is not configured; missing: {', '.join(missing)}",
                    retryable=False,
                    details={"missing": missing},
                )
            )

        body = self._build_body(system_prompt, user_prompt)
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        if len(encoded) > MAX_REQUEST_BYTES:
            return ProviderResult.failure(
                ProviderError(
                    provider=self.name,
                    code=ProviderErrorCode.BAD_REQUEST,
                    message=(
                        f"Prompt of {len(encoded)} bytes exceeds the "
                        f"{MAX_REQUEST_BYTES}-byte request ceiling"
                    ),
                    retryable=False,
                )
            )

        max_attempts = max(1, settings.nine_router_max_attempts)
        timeout_s = settings.nine_router_timeout_ms / 1000.0
        started = time.monotonic()
        last_error: ProviderError | None = None

        for attempt in range(1, max_attempts + 1):
            attempt_result = self._attempt(encoded, timeout_s, attempt)
            if attempt_result.ok:
                elapsed = int((time.monotonic() - started) * 1000)
                logger.info(
                    "9router call succeeded (attempt %d/%d, %d ms)",
                    attempt,
                    max_attempts,
                    elapsed,
                )
                return ProviderResult.success(
                    attempt_result.unwrap(), mocked=False, elapsed_ms=elapsed
                )

            error = attempt_result.error
            assert error is not None
            last_error = error
            # Log the code, never the prompt or the header.
            logger.warning(
                "9router attempt %d/%d failed: %s",
                attempt,
                max_attempts,
                error.describe(),
            )
            if not error.retryable or attempt == max_attempts:
                break
            self._backoff(attempt)

        elapsed = int((time.monotonic() - started) * 1000)
        assert last_error is not None
        if last_error.retryable and max_attempts > 1:
            # Distinguish "we gave up after retrying" from "failed once and
            # stopped", so the UI can explain which actually happened.
            final = ProviderError(
                provider=self.name,
                code=ProviderErrorCode.RETRIES_EXHAUSTED,
                message=f"Gave up after {max_attempts} attempts; last error: {last_error.message}",
                retryable=True,
                attempts=max_attempts,
                details={"lastCode": last_error.code.value},
            )
        else:
            final = ProviderError(
                provider=last_error.provider,
                code=last_error.code,
                message=last_error.message,
                retryable=last_error.retryable,
                attempts=max_attempts if last_error.retryable else 1,
                details=last_error.details,
            )
        return ProviderResult.failure(final, elapsed_ms=elapsed)

    # -- internals --------------------------------------------------------

    def _build_body(self, system_prompt: str, user_prompt: str) -> dict[str, object]:
        """Build the OpenAI-compatible request body.

        ``temperature`` is 0 because a task plan is a structured artefact, not
        creative text: determinism makes the planner testable and makes a
        replayed request produce the same plan digest.
        """
        return {
            "model": self._settings.nine_router_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
            "max_tokens": self._settings.nine_router_max_output_tokens,
            "stream": False,
            # Best effort: OpenAI-compatible gateways that support it will
            # constrain the reply to JSON. We never rely on this - the reply is
            # schema-validated locally either way.
            "response_format": {"type": "json_object"},
        }

    def _attempt(self, encoded_body: bytes, timeout_s: float, attempt: int) -> ProviderResult[str]:
        request = urllib.request.Request(
            self._settings.nine_router_url,
            data=encoded_body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {self._settings.nine_router_api_key}",
                "User-Agent": "project-atlas/0.1 (atlas-core; local backend)",
            },
        )

        try:
            response = self._opener(request, timeout_s)
        except urllib.error.HTTPError as exc:  # HTTPError is also a response
            return self._classify_http_error(exc)
        except socket.timeout:
            return ProviderResult.failure(
                ProviderError(
                    provider=self.name,
                    code=ProviderErrorCode.TIMEOUT,
                    message=f"No response within {timeout_s:.1f}s",
                    attempts=attempt,
                )
            )
        except urllib.error.URLError as exc:
            # URLError wraps socket.timeout on some Python/OS combinations.
            if isinstance(exc.reason, socket.timeout):
                code = ProviderErrorCode.TIMEOUT
            else:
                code = ProviderErrorCode.NETWORK
            return ProviderResult.failure(
                ProviderError(
                    provider=self.name,
                    code=code,
                    message=f"Transport failure: {exc.reason}",
                    attempts=attempt,
                )
            )
        except (OSError, ValueError) as exc:
            return ProviderResult.failure(
                ProviderError(
                    provider=self.name,
                    code=ProviderErrorCode.NETWORK,
                    message=f"Transport failure: {exc}",
                    attempts=attempt,
                )
            )

        try:
            raw = response.read(MAX_RESPONSE_BYTES + 1)  # type: ignore[attr-defined]
        except (socket.timeout, TimeoutError):
            return ProviderResult.failure(
                ProviderError(
                    provider=self.name,
                    code=ProviderErrorCode.TIMEOUT,
                    message=f"Response body not read within {timeout_s:.1f}s",
                    attempts=attempt,
                )
            )
        except OSError as exc:
            return ProviderResult.failure(
                ProviderError(
                    provider=self.name,
                    code=ProviderErrorCode.NETWORK,
                    message=f"Failed reading response body: {exc}",
                    attempts=attempt,
                )
            )
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

        if len(raw) > MAX_RESPONSE_BYTES:
            return ProviderResult.failure(
                ProviderError(
                    provider=self.name,
                    code=ProviderErrorCode.RESPONSE_TOO_LARGE,
                    message=f"Response exceeded {MAX_RESPONSE_BYTES} bytes",
                    retryable=False,
                    attempts=attempt,
                )
            )

        return self._parse_success_body(raw, attempt)

    def _parse_success_body(self, raw: bytes, attempt: int) -> ProviderResult[str]:
        try:
            parsed = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            return ProviderResult.failure(
                ProviderError(
                    provider=self.name,
                    code=ProviderErrorCode.MALFORMED_RESPONSE,
                    message=f"Response body was not JSON: {exc.msg}",
                    retryable=False,
                    attempts=attempt,
                )
            )

        if not isinstance(parsed, dict):
            return ProviderResult.failure(
                ProviderError(
                    provider=self.name,
                    code=ProviderErrorCode.MALFORMED_RESPONSE,
                    message="Response JSON was not an object",
                    retryable=False,
                    attempts=attempt,
                )
            )

        # An OpenAI-compatible gateway may report an application error with
        # HTTP 200. Treat that as a failure, never as empty success.
        error_field = parsed.get("error")
        if error_field:
            message = ""
            if isinstance(error_field, dict):
                message = str(error_field.get("message") or error_field)
            else:
                message = str(error_field)
            return ProviderResult.failure(
                ProviderError(
                    provider=self.name,
                    code=ProviderErrorCode.UPSTREAM,
                    message=f"Gateway reported an error: {message}",
                    attempts=attempt,
                )
            )

        text = self._extract_message_text(parsed)
        if text is None:
            return ProviderResult.failure(
                ProviderError(
                    provider=self.name,
                    code=ProviderErrorCode.MALFORMED_RESPONSE,
                    message="Response had no choices[0].message.content string",
                    retryable=False,
                    attempts=attempt,
                )
            )
        if not text.strip():
            return ProviderResult.failure(
                ProviderError(
                    provider=self.name,
                    code=ProviderErrorCode.MALFORMED_RESPONSE,
                    message="Assistant message content was empty",
                    retryable=False,
                    attempts=attempt,
                )
            )

        self.last_usage = self._extract_usage(parsed)
        return ProviderResult.success(text)

    @staticmethod
    def _extract_message_text(parsed: dict[str, object]) -> str | None:
        choices = parsed.get("choices")
        if not isinstance(choices, list) or not choices:
            return None
        first = choices[0]
        if not isinstance(first, dict):
            return None
        message = first.get("message")
        if not isinstance(message, dict):
            return None
        content = message.get("content")
        if isinstance(content, str):
            return content
        # Some gateways return content as a list of typed parts.
        if isinstance(content, list):
            parts = [
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            ]
            if parts:
                return "".join(parts)
        return None

    @staticmethod
    def _extract_usage(parsed: dict[str, object]) -> ChatUsage:
        usage = parsed.get("usage")
        if not isinstance(usage, dict):
            return ChatUsage()

        def count(key: str) -> int:
            value = usage.get(key)
            return value if isinstance(value, int) and not isinstance(value, bool) else 0

        return ChatUsage(
            prompt_tokens=count("prompt_tokens"),
            completion_tokens=count("completion_tokens"),
            total_tokens=count("total_tokens"),
        )

    def _classify_http_error(self, exc: urllib.error.HTTPError) -> ProviderResult[str]:
        status = exc.code
        try:
            detail = exc.read(4096).decode("utf-8", errors="replace")
        except Exception:  # pragma: no cover - body may be unavailable
            detail = ""
        detail = redact(detail).strip()

        if status in (401, 403):
            code = ProviderErrorCode.AUTH
        elif status == 429:
            code = ProviderErrorCode.RATE_LIMIT
        elif status >= 500:
            code = ProviderErrorCode.UPSTREAM
        else:
            code = ProviderErrorCode.BAD_REQUEST

        message = f"HTTP {status} from 9Router"
        if detail:
            message = f"{message}: {detail[:300]}"
        return ProviderResult.failure(
            ProviderError(provider=self.name, code=code, message=message)
        )

    def _backoff(self, attempt: int) -> None:
        """Exponential backoff with jitter, capped.

        Jitter matters even for a single client: a rate-limited gateway should
        not be hit again on a fixed schedule.
        """
        delay = min(RETRY_BASE_DELAY_S * (2 ** (attempt - 1)), RETRY_MAX_DELAY_S)
        self._sleep(delay + random.uniform(0, delay / 2))


class MockNineRouterClient:
    """Deterministic offline stand-in for 9Router (``ATLAS_MOCK_MODE=true``).

    Clearly labelled as a mock: results carry ``mocked=True`` and
    :attr:`is_mock` is True, so no caller can mistake it for a live
    integration. Used for the offline/demo path and for failure-injection
    tests.
    """

    name = PROVIDER_NINE_ROUTER
    is_mock = True

    def __init__(
        self,
        *,
        responses: list[str] | None = None,
        errors: list[ProviderError] | None = None,
    ) -> None:
        """
        ``responses``/``errors`` are consumed in order, one per call, so a test
        can script "fail, fail, then succeed". When ``responses`` is exhausted
        the last entry repeats.
        """
        self._responses = list(responses or [])
        self._errors = list(errors or [])
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_prompt: str) -> ProviderResult[str]:
        self.calls.append((system_prompt, user_prompt))
        if self._errors:
            error = self._errors.pop(0)
            return ProviderResult.failure(error, mocked=True)
        if not self._responses:
            return ProviderResult.failure(
                ProviderError(
                    provider=self.name,
                    code=ProviderErrorCode.NOT_CONFIGURED,
                    message="MockNineRouterClient has no scripted response",
                    retryable=False,
                ),
                mocked=True,
            )
        text = self._responses[0] if len(self._responses) == 1 else self._responses.pop(0)
        return ProviderResult.success(text, mocked=True)


def build_chat_provider(settings: ProviderSettings) -> tuple[object, bool]:
    """Return ``(provider, is_live)`` for the given settings.

    Single decision point for live-vs-mock so the rule cannot drift between
    call sites: live only when mock mode is off **and** all three
    ``NINE_ROUTER_*`` values are present.
    """
    if settings.use_live_llm:
        return NineRouterClient(settings), True
    return MockNineRouterClient(), False
