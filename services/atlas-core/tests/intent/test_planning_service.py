"""PlanningService - the seam the orchestrator consumes.

OWNER: Person 2 - Voice, Intent & 9Router.

This is the contract test for what Person 1, Person 3, and Person 4 integrate
against: one call in, an ordered list of schema-valid events out, plus a
validated plan and the digest that ``POST /confirm`` must echo.
"""

from __future__ import annotations

import json

import pytest

from atlas.planner.base import PlanOutcomeKind
from atlas.planner.contracts import validate_event, validator_info
from atlas.planner.llm_planner import LlmPlanner
from atlas.planner.service import PlanningService
from atlas.providers.base import ProviderError, ProviderErrorCode
from atlas.providers.nine_router import MockNineRouterClient
from atlas.providers.settings import ProviderSettings

TASK_ID = "task-svc-1"


def _assert_events_valid(events):
    info = validator_info()
    if not info.events_supported:
        pytest.skip(f"event schema validation unavailable: {info.describe()}")
    for event in events:
        violations = validate_event(event)
        assert not violations, f"{event['type']}: " + "; ".join(str(v) for v in violations)


@pytest.fixture
def service(mock_settings):
    return PlanningService(mock_settings)


# -- text planning --------------------------------------------------------


def test_text_request_produces_plan_and_ordered_events(service):
    result = service.plan_text(TASK_ID, "open my editor")

    assert result.ok
    assert [e["type"] for e in result.events] == ["transcript", "plan_ready", "confirmation_required"]
    _assert_events_valid(result.events)


def test_transcript_event_is_emitted_before_planning(service):
    """The UI must be able to show the request even if planning then fails."""
    result = service.plan_text(TASK_ID, "something entirely unsupported")

    assert result.events[0]["type"] == "transcript"
    assert result.events[0]["payload"]["source"] == "text"
    assert result.events[0]["payload"]["isFinal"] is True


def test_plan_digest_is_exposed_for_confirmation(service):
    result = service.plan_text(TASK_ID, "open my editor")

    assert result.plan_digest.startswith("sha256:")
    assert result.plan_digest == result.events[1]["payload"]["planDigest"]
    assert service.verify_plan_digest(result.plan, result.plan_digest)


def test_digest_check_rejects_a_substituted_plan(service):
    result = service.plan_text(TASK_ID, "open my editor")
    tampered = json.loads(json.dumps(result.plan))
    tampered["steps"][0]["arguments"]["appId"] = "cmd"

    assert not service.verify_plan_digest(tampered, result.plan_digest)


def test_all_events_carry_the_task_id(service):
    result = service.plan_text(TASK_ID, "open my editor")
    assert all(event["taskId"] == TASK_ID for event in result.events)


def test_missing_task_id_is_a_programming_error(service):
    """The orchestrator owns task identity; planning must not invent one."""
    with pytest.raises(ValueError):
        service.plan_text("", "open my editor")


# -- clarification paths --------------------------------------------------


def test_blank_request_asks_instead_of_planning(service):
    result = service.plan_text(TASK_ID, "   ")

    assert result.outcome.kind is PlanOutcomeKind.CLARIFICATION
    assert [e["type"] for e in result.events] == ["transcript", "clarification_required"]
    _assert_events_valid(result.events)


def test_none_request_is_handled(service):
    result = service.plan_text(TASK_ID, None)
    assert result.outcome.kind is PlanOutcomeKind.CLARIFICATION


def test_unsupported_request_asks_instead_of_guessing(service):
    result = service.plan_text(TASK_ID, "reorganise my whole photo library")

    assert result.outcome.kind is PlanOutcomeKind.CLARIFICATION
    assert result.plan is None


def test_blocked_request_never_yields_a_plan(service):
    for request in ("delete everything in Documents", "run a shell command", "enter my password"):
        result = service.plan_text(TASK_ID, request)
        assert result.plan is None, request
        assert not result.ok, request


# -- mock vs live labelling ----------------------------------------------


def test_mock_mode_is_reported(service):
    assert service.mock_mode is True
    assert service.planner_name == "fixture"
    assert service.plan_text(TASK_ID, "open my editor").mocked is True


def test_live_settings_select_the_llm_planner(live_settings):
    svc = PlanningService(live_settings)

    assert svc.mock_mode is False
    assert svc.planner_name == "9router"


def test_mock_mode_off_without_a_key_falls_back_to_fixtures():
    """Fail safe: an unconfigured live mode must not look live."""
    svc = PlanningService(ProviderSettings(mock_mode=False))

    assert svc.mock_mode is True
    assert svc.planner_name == "fixture"


def test_injected_planner_is_used(mock_settings, registry):
    plan_json = json.dumps(
        {
            "taskId": TASK_ID,
            "userRequest": "open my editor",
            "summary": "Open the allowlisted editor application.",
            "requiresConfirmation": True,
            "steps": [
                {
                    "stepId": 1,
                    "actionType": "app.open",
                    "arguments": {"appId": "notepad"},
                    "riskTier": "T1",
                    "preconditions": [{"type": "app.allowlisted", "args": {"appId": "notepad"}}],
                    "postconditions": [{"type": "window.exists", "args": {"appId": "notepad"}}],
                    "timeoutMs": 10000,
                    "idempotencyKey": f"{TASK_ID}-step-1",
                }
            ],
        }
    )
    planner = LlmPlanner(MockNineRouterClient(responses=[plan_json]), registry)
    svc = PlanningService(mock_settings, planner=planner)

    result = svc.plan_text(TASK_ID, "open my editor")
    assert result.ok
    assert result.mocked is True


# -- provider failure ----------------------------------------------------


def test_provider_failure_emits_provider_error_and_no_plan(mock_settings, registry):
    planner = LlmPlanner(
        MockNineRouterClient(
            errors=[
                ProviderError(
                    provider="9router", code=ProviderErrorCode.TIMEOUT, message="no response"
                )
            ]
        ),
        registry,
    )
    svc = PlanningService(mock_settings, planner=planner)

    result = svc.plan_text(TASK_ID, "open my editor")

    assert not result.ok
    assert result.plan is None
    assert [e["type"] for e in result.events] == ["transcript", "provider_error"]
    assert result.events[1]["payload"]["code"] == "TIMEOUT"
    _assert_events_valid(result.events)


def test_local_rejection_is_not_reported_as_a_provider_error(mock_settings, registry):
    bad = json.dumps(
        {
            "taskId": TASK_ID,
            "userRequest": "open my editor",
            "summary": "s",
            "requiresConfirmation": True,
            "steps": [
                {
                    "stepId": 1,
                    "actionType": "shell.exec",
                    "arguments": {},
                    "riskTier": "T3",
                    "preconditions": [],
                    "postconditions": [],
                    "timeoutMs": 1000,
                    "idempotencyKey": "k1",
                }
            ],
        }
    )
    planner = LlmPlanner(MockNineRouterClient(responses=[bad]), registry)
    svc = PlanningService(mock_settings, planner=planner)

    result = svc.plan_text(TASK_ID, "open my editor")

    assert not result.ok
    assert [e["type"] for e in result.events] == ["transcript"]


# -- voice path -----------------------------------------------------------


def test_voice_request_plans_from_the_mock_transcript(service):
    result = service.plan_voice(TASK_ID, b"fake-audio-bytes")

    assert result.ok
    assert result.events[0]["payload"]["source"] == "voice"
    assert [e["type"] for e in result.events] == ["transcript", "plan_ready", "confirmation_required"]
    _assert_events_valid(result.events)


def test_voice_failure_falls_back_to_text_instead_of_failing(mock_settings):
    """Master PRD FR-01: text stays usable when speech fails."""
    from atlas.providers.aws_transcribe import AwsTranscribeProvider

    svc = PlanningService(mock_settings, speech_to_text=AwsTranscribeProvider(mock_settings))
    result = svc.plan_voice(TASK_ID, b"audio")

    assert result.outcome.kind is PlanOutcomeKind.CLARIFICATION
    types = [e["type"] for e in result.events]
    assert types == ["provider_error", "clarification_required"]
    assert "type" in result.events[1]["payload"]["question"].lower()
    _assert_events_valid(result.events)


def test_empty_audio_asks_the_user_to_retry(service):
    result = service.plan_voice(TASK_ID, b"")

    assert result.outcome.kind is PlanOutcomeKind.CLARIFICATION
    assert result.plan is None


def test_voice_availability_is_reported(service):
    assert service.voice_available is True


# -- diagnostics ----------------------------------------------------------


def test_diagnostics_are_json_safe_and_secret_free(live_settings):
    svc = PlanningService(live_settings)
    encoded = json.dumps(svc.diagnostics())

    assert "sk-test-key-value-0123456789" not in encoded
    assert "nineRouterApiKeyPresent" in encoded


def test_diagnostics_state_what_is_mocked(service):
    diagnostics = service.diagnostics()

    assert diagnostics["mockMode"] is True
    assert diagnostics["voice"]["mocked"] is True
    # Honesty requirement: never imply a working live integration.
    assert diagnostics["voice"]["liveCallImplemented"] is False
    assert diagnostics["speech"]["liveCallImplemented"] is False


def test_diagnostics_report_the_contract_and_registry(service, registry):
    diagnostics = service.diagnostics()

    assert diagnostics["registeredActions"] == list(registry.action_types)
    assert diagnostics["contractVersion"] == "1.0.0"
    assert "credential.entry" in diagnostics["blockedCategories"]


def test_from_env_defaults_to_mock_mode(monkeypatch):
    """An empty environment must not produce a live-looking service."""
    svc = PlanningService.from_env()
    assert svc.mock_mode is True


def test_for_mock_mode_is_explicitly_offline():
    assert PlanningService.for_mock_mode().mock_mode is True


# -- optional spoken feedback --------------------------------------------


def test_speak_returns_mock_audio_in_mock_mode(service):
    audio, reason = service.speak("Opening Notepad.")

    assert audio is not None
    assert audio.startswith(b"ATLAS-MOCK-AUDIO:")
    assert reason == ""


def test_speak_reports_a_reason_when_unavailable(mock_settings):
    from atlas.providers.aws_polly import AwsPollyProvider

    svc = PlanningService(mock_settings, text_to_speech=AwsPollyProvider(mock_settings))
    audio, reason = svc.speak("Opening Notepad.")

    assert audio is None
    assert reason


def test_speak_never_raises_on_a_broken_provider(mock_settings):
    class Exploding:
        name = "aws-polly"
        available = True

        def synthesize(self, text, *, voice_id="Joanna"):
            raise RuntimeError("boom")

    svc = PlanningService(mock_settings, text_to_speech=Exploding())
    audio, reason = svc.speak("hello")

    assert audio is None
    assert "RuntimeError" in reason
