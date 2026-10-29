"""9Router client: bounded timeouts, retries, failure taxonomy, redaction.

OWNER: Person 2 - Voice, Intent & 9Router.

Acceptance checklist items covered:
- "9Router is called from backend only." (asserted structurally: see
  ``test_no_frontend_file_references_the_api_key``)
- "Provider timeouts and bounded retries work."
- "No secrets appear in logs."

Two test strategies, deliberately both:

* A **real local HTTP server** (``http.server`` on 127.0.0.1, ephemeral port)
  proves the actual ``urllib`` request/response path works - headers, body
  encoding, JSON parsing, status handling. No mocking of our own transport.
* An **injected opener** drives timeout/backoff/retry paths deterministically
  and without real delays.

No test contacts the real 9Router gateway. The live endpoint is still OPEN in
``docs/decisions.md`` (M-18 Q2/Q6), so the live integration remains unverified
and is reported as such.
"""

from __future__ import annotations

import json
import socket
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from conftest import REPO_ROOT, STUB_CREDENTIAL, make_settings

from atlas.providers.base import ProviderErrorCode
from atlas.providers.nine_router import (
    MAX_RESPONSE_BYTES,
    MockNineRouterClient,
    NineRouterClient,
    build_chat_provider,
)
from atlas.providers.redaction import register_secret
from atlas.providers.settings import ProviderSettings

# -- local stub gateway ---------------------------------------------------


class _StubGateway:
    """Minimal OpenAI-compatible stub served over real HTTP on localhost."""

    def __init__(self, responder):
        self._responder = responder
        self.requests: list[dict] = []
        handler = self._make_handler()
        self._server = HTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def _make_handler(self):
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                gateway.requests.append(
                    {
                        "path": self.path,
                        "headers": dict(self.headers),
                        "body": json.loads(raw.decode("utf-8")) if raw else {},
                    }
                )
                status, payload = gateway._responder(len(gateway.requests))
                body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):  # silence stub access logs
                pass

        return Handler

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def _chat_payload(content: str) -> dict:
    return {
        "id": "chatcmpl-stub",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def _settings_for(base_url: str, **overrides) -> ProviderSettings:
    defaults = dict(
        mock_mode=False,
        nine_router_base_url=base_url,
        nine_router_model="stub-model",
        nine_router_timeout_ms=3_000,
        nine_router_max_attempts=3,
    )
    defaults.update(overrides)
    credential = defaults.pop("credential", STUB_CREDENTIAL)
    return make_settings(credential, **defaults)


# -- real HTTP happy path -------------------------------------------------


def test_successful_call_over_real_http():
    with _StubGateway(lambda n: (200, _chat_payload('{"ok": true}'))) as gateway:
        client = NineRouterClient(_settings_for(gateway.base_url), sleep=lambda _: None)
        result = client.complete("system", "user")

    assert result.ok
    assert result.unwrap() == '{"ok": true}'
    assert result.mocked is False


def test_request_uses_the_openai_compatible_contract():
    with _StubGateway(lambda n: (200, _chat_payload("{}"))) as gateway:
        settings = _settings_for(gateway.base_url)
        NineRouterClient(settings, sleep=lambda _: None).complete("SYS", "USR")
        recorded = gateway.requests[0]

    assert recorded["path"] == "/v1/chat/completions"
    body = recorded["body"]
    assert body["model"] == "stub-model"
    assert body["stream"] is False
    # temperature 0: a plan is a structured artefact, and determinism keeps the
    # plan digest stable across identical requests.
    assert body["temperature"] == 0
    assert body["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USR"},
    ]


def test_authorization_header_is_sent_as_bearer():
    with _StubGateway(lambda n: (200, _chat_payload("{}"))) as gateway:
        settings = _settings_for(gateway.base_url)
        NineRouterClient(settings, sleep=lambda _: None).complete("s", "u")
        headers = gateway.requests[0]["headers"]

    assert headers["Authorization"] == f"Bearer {STUB_CREDENTIAL}"


def test_custom_chat_path_is_honoured():
    """The deployment path is still unconfirmed, so it must be overridable."""
    with _StubGateway(lambda n: (200, _chat_payload("{}"))) as gateway:
        settings = _settings_for(gateway.base_url, nine_router_chat_path="/api/v2/chat")
        NineRouterClient(settings, sleep=lambda _: None).complete("s", "u")

    assert gateway.requests[0]["path"] == "/api/v2/chat"


def test_usage_is_recorded_for_budget_accounting():
    with _StubGateway(lambda n: (200, _chat_payload("{}"))) as gateway:
        client = NineRouterClient(_settings_for(gateway.base_url), sleep=lambda _: None)
        client.complete("s", "u")

    assert client.last_usage is not None
    assert client.last_usage.total_tokens == 15


def test_content_returned_as_typed_parts_is_joined():
    payload = {
        "choices": [
            {"message": {"content": [{"type": "text", "text": '{"a":'}, {"type": "text", "text": "1}"}]}}
        ]
    }
    with _StubGateway(lambda n: (200, payload)) as gateway:
        client = NineRouterClient(_settings_for(gateway.base_url), sleep=lambda _: None)
        result = client.complete("s", "u")

    assert result.unwrap() == '{"a":1}'


# -- configuration gate --------------------------------------------------


@pytest.mark.parametrize(
    "missing",
    ["nine_router_base_url", "nine_router_model", "credential"],
    ids=["no-base-url", "no-model", "no-credential"],
)
def test_unconfigured_client_fails_without_a_network_call(missing):
    settings = _settings_for("http://127.0.0.1:1", **{missing: ""})

    def explode(*_args, **_kwargs):
        raise AssertionError("no HTTP request may be attempted when unconfigured")

    result = NineRouterClient(settings, opener=explode).complete("s", "u")

    assert not result.ok
    assert result.error.code is ProviderErrorCode.NOT_CONFIGURED
    assert result.error.retryable is False


def test_missing_keys_are_named_in_the_error():
    settings = _settings_for("", nine_router_model="", credential="")
    result = NineRouterClient(settings).complete("s", "u")

    assert "NINE_ROUTER_BASE_URL" in result.error.message
    assert "NINE_ROUTER_MODEL" in result.error.message
    assert "NINE_ROUTER_API_KEY" in result.error.message


# -- HTTP error taxonomy -------------------------------------------------


@pytest.mark.parametrize(
    "status,expected,retryable",
    [
        (401, ProviderErrorCode.AUTH, False),
        (403, ProviderErrorCode.AUTH, False),
        (400, ProviderErrorCode.BAD_REQUEST, False),
        (404, ProviderErrorCode.BAD_REQUEST, False),
    ],
)
def test_non_retryable_http_errors_are_classified(status, expected, retryable):
    with _StubGateway(lambda n: (status, {"error": {"message": "nope"}})) as gateway:
        settings = _settings_for(gateway.base_url)
        client = NineRouterClient(settings, sleep=lambda _: None)
        result = client.complete("s", "u")

    assert result.error.code is expected
    assert result.error.retryable is retryable
    # A non-retryable error must be attempted exactly once.
    assert len(gateway.requests) == 1


@pytest.mark.parametrize(
    "status,expected",
    [(429, ProviderErrorCode.RATE_LIMIT), (500, ProviderErrorCode.UPSTREAM), (503, ProviderErrorCode.UPSTREAM)],
)
def test_retryable_http_errors_exhaust_the_budget(status, expected):
    with _StubGateway(lambda n: (status, {"error": {"message": "later"}})) as gateway:
        settings = _settings_for(gateway.base_url, nine_router_max_attempts=3)
        result = NineRouterClient(settings, sleep=lambda _: None).complete("s", "u")

    assert len(gateway.requests) == 3, "retryable errors must use the full budget"
    assert result.error.code is ProviderErrorCode.RETRIES_EXHAUSTED
    assert result.error.attempts == 3
    assert expected.value in result.error.details["lastCode"]


def test_retry_then_success():
    def responder(call_number: int):
        if call_number == 1:
            return 503, {"error": {"message": "warming up"}}
        return 200, _chat_payload('{"recovered": true}')

    with _StubGateway(responder) as gateway:
        settings = _settings_for(gateway.base_url, nine_router_max_attempts=3)
        result = NineRouterClient(settings, sleep=lambda _: None).complete("s", "u")

    assert result.ok
    assert result.unwrap() == '{"recovered": true}'
    assert len(gateway.requests) == 2, "must stop retrying once it succeeds"


def test_max_attempts_of_one_disables_retry():
    with _StubGateway(lambda n: (503, {"error": {}})) as gateway:
        settings = _settings_for(gateway.base_url, nine_router_max_attempts=1)
        result = NineRouterClient(settings, sleep=lambda _: None).complete("s", "u")

    assert len(gateway.requests) == 1
    assert result.error.code is ProviderErrorCode.UPSTREAM


# -- malformed responses -------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        b"not json at all",
        b"[1, 2, 3]",
        b"{}",
        b'{"choices": []}',
        b'{"choices": [{}]}',
        b'{"choices": [{"message": {}}]}',
        b'{"choices": [{"message": {"content": ""}}]}',
        b'{"choices": [{"message": {"content": "   "}}]}',
    ],
)
def test_malformed_body_is_not_treated_as_success(payload):
    """Master PRD 10.4: a provider failure must not become a success."""
    with _StubGateway(lambda n: (200, payload)) as gateway:
        settings = _settings_for(gateway.base_url)
        result = NineRouterClient(settings, sleep=lambda _: None).complete("s", "u")

    assert not result.ok
    assert result.error.code is ProviderErrorCode.MALFORMED_RESPONSE
    assert result.error.retryable is False
    assert len(gateway.requests) == 1, "a malformed body must not be retried"


def test_application_error_with_http_200_is_a_failure():
    """Some gateways report failure in the body with a 200 status."""
    with _StubGateway(lambda n: (200, {"error": {"message": "model unavailable"}})) as gateway:
        settings = _settings_for(gateway.base_url, nine_router_max_attempts=1)
        result = NineRouterClient(settings, sleep=lambda _: None).complete("s", "u")

    assert not result.ok
    assert result.error.code is ProviderErrorCode.UPSTREAM
    assert "model unavailable" in result.error.message


def test_oversized_response_is_refused():
    big = json.dumps(_chat_payload("x" * (MAX_RESPONSE_BYTES + 100))).encode("utf-8")
    with _StubGateway(lambda n: (200, big)) as gateway:
        settings = _settings_for(gateway.base_url, nine_router_max_attempts=1)
        result = NineRouterClient(settings, sleep=lambda _: None).complete("s", "u")

    assert result.error.code is ProviderErrorCode.RESPONSE_TOO_LARGE
    assert result.error.retryable is False


def test_oversized_request_is_refused_before_sending():
    def explode(*_args, **_kwargs):
        raise AssertionError("an oversized prompt must not be sent")

    settings = _settings_for("http://127.0.0.1:1")
    result = NineRouterClient(settings, opener=explode).complete("s", "u" * 300_000)

    assert result.error.code is ProviderErrorCode.BAD_REQUEST
    assert result.error.retryable is False


# -- timeouts and transport ----------------------------------------------


def test_socket_timeout_is_classified_and_retried():
    attempts = {"n": 0}

    def timing_out(request, timeout):
        attempts["n"] += 1
        raise socket.timeout("timed out")

    settings = _settings_for("http://127.0.0.1:1", nine_router_max_attempts=3)
    result = NineRouterClient(settings, opener=timing_out, sleep=lambda _: None).complete("s", "u")

    assert attempts["n"] == 3
    assert result.error.code is ProviderErrorCode.RETRIES_EXHAUSTED
    assert result.error.details["lastCode"] == ProviderErrorCode.TIMEOUT.value


def test_urlerror_wrapping_a_timeout_is_classified_as_timeout():
    def timing_out(request, timeout):
        raise urllib.error.URLError(socket.timeout("timed out"))

    settings = _settings_for("http://127.0.0.1:1", nine_router_max_attempts=1)
    result = NineRouterClient(settings, opener=timing_out, sleep=lambda _: None).complete("s", "u")

    assert result.error.code is ProviderErrorCode.TIMEOUT


def test_connection_refused_is_a_network_error():
    """Nothing is listening on port 1, so this exercises the real socket path."""
    settings = _settings_for("http://127.0.0.1:1", nine_router_max_attempts=1)
    result = NineRouterClient(settings, sleep=lambda _: None).complete("s", "u")

    assert not result.ok
    assert result.error.code is ProviderErrorCode.NETWORK
    assert result.error.retryable is True


def test_configured_timeout_is_passed_to_the_transport():
    seen: list[float] = []

    def capture(request, timeout):
        seen.append(timeout)
        raise socket.timeout("timed out")

    settings = _settings_for("http://127.0.0.1:1", nine_router_timeout_ms=4_500, nine_router_max_attempts=1)
    NineRouterClient(settings, opener=capture, sleep=lambda _: None).complete("s", "u")

    assert seen == [4.5]


def test_backoff_grows_and_is_capped():
    delays: list[float] = []

    def always_timeout(request, timeout):
        raise socket.timeout("timed out")

    settings = _settings_for("http://127.0.0.1:1", nine_router_max_attempts=5)
    NineRouterClient(settings, opener=always_timeout, sleep=delays.append).complete("s", "u")

    # 4 sleeps for 5 attempts; strictly increasing lower bounds, all capped.
    assert len(delays) == 4
    assert all(0 < d <= 6.0 for d in delays)
    assert delays[0] < delays[-1]


# -- redaction -----------------------------------------------------------


def test_error_body_from_the_gateway_is_redacted():
    register_secret(STUB_CREDENTIAL)
    leak = {"error": {"message": f"invalid key {STUB_CREDENTIAL} supplied"}}

    with _StubGateway(lambda n: (401, leak)) as gateway:
        settings = _settings_for(gateway.base_url)
        result = NineRouterClient(settings, sleep=lambda _: None).complete("s", "u")

    assert STUB_CREDENTIAL not in result.error.message
    assert STUB_CREDENTIAL not in json.dumps(result.error.to_event_payload())


def test_prompt_is_never_logged(caplog):
    import logging

    with _StubGateway(lambda n: (503, {"error": {}})) as gateway:
        settings = _settings_for(gateway.base_url, nine_router_max_attempts=2)
        with caplog.at_level(logging.DEBUG):
            NineRouterClient(settings, sleep=lambda _: None).complete(
                "SYSTEM_PROMPT_MARKER", "USER_PROMPT_MARKER"
            )

    assert "USER_PROMPT_MARKER" not in caplog.text
    assert "SYSTEM_PROMPT_MARKER" not in caplog.text
    assert "Bearer" not in caplog.text


# -- backend-only enforcement -------------------------------------------


def test_no_frontend_file_references_the_api_key():
    """Acceptance item: "9Router is called from backend only."

    Structural assertion rather than a claim: no file under
    ``apps/desktop-ui/`` may mention a 9Router credential or endpoint. Person 3
    owns that tree, so this test reports a contract breach rather than fixing it.
    """
    ui_root = REPO_ROOT / "apps" / "desktop-ui"
    if not ui_root.exists():  # pragma: no cover - layout guard
        pytest.skip("apps/desktop-ui is not present")

    offenders: list[str] = []
    for path in ui_root.rglob("*"):
        if not path.is_file() or "node_modules" in path.parts:
            continue
        if path.suffix.lower() not in {".ts", ".tsx", ".js", ".jsx", ".json", ".html", ".rs", ".env"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:  # pragma: no cover
            continue
        if "NINE_ROUTER_API_KEY" in text or "chat/completions" in text:
            offenders.append(str(path.relative_to(REPO_ROOT)))

    assert not offenders, f"9Router must stay backend-only; found references in {offenders}"


# -- provider selection --------------------------------------------------


def test_mock_mode_selects_the_mock_client(mock_settings):
    provider, is_live = build_chat_provider(mock_settings)

    assert is_live is False
    assert isinstance(provider, MockNineRouterClient)


def test_configured_live_settings_select_the_real_client(live_settings):
    provider, is_live = build_chat_provider(live_settings)

    assert is_live is True
    assert isinstance(provider, NineRouterClient)


def test_mock_mode_off_but_unconfigured_still_selects_the_mock():
    """Fail safe: a missing key must not produce a broken live client."""
    settings = ProviderSettings(mock_mode=False)  # no URL/model/key
    provider, is_live = build_chat_provider(settings)

    assert is_live is False
    assert isinstance(provider, MockNineRouterClient)


# -- mock client behaviour ----------------------------------------------


def test_mock_client_labels_its_results():
    client = MockNineRouterClient(responses=['{"ok": 1}'])
    result = client.complete("s", "u")

    assert result.ok
    assert result.mocked is True, "a mock result must never look like a live call"
    assert client.is_mock is True


def test_mock_client_scripts_errors_then_responses():
    from atlas.providers.base import ProviderError

    client = MockNineRouterClient(
        errors=[ProviderError(provider="9router", code=ProviderErrorCode.TIMEOUT)]
    )
    assert not client.complete("s", "u").ok


def test_mock_client_without_a_script_fails_loudly():
    result = MockNineRouterClient().complete("s", "u")

    assert not result.ok
    assert result.error.code is ProviderErrorCode.NOT_CONFIGURED
