"""PlanningService - the single entry point the orchestrator calls.

OWNER: Person 2 - Voice, Intent & 9Router.

One call does the whole intent half of ``POST /api/v1/tasks/plan``:

    normalize -> transcript event -> plan (live or fixture) -> validate
              -> plan_ready / clarification_required / provider_error

Wiring request for the project owner (``INSTRUCTIONS.md`` section 1 - the
orchestrator and API are owner-owned, so this is proposed, not wired):
see ``reference/person-2-voice-9router/docs/change-requests/``.

    from atlas.planner import PlanningService

    service = PlanningService.from_env()          # once, at startup
    result = service.plan_text(task_id, user_request)
    for event in result.events:                   # emit in order, via SSE
        ...
    if result.outcome.ok:
        plan = result.outcome.plan                # schema-valid TaskPlan
        digest = result.outcome.plan_digest       # echo required by /confirm

What this service deliberately does **not** do: decide whether an action is
authorized, resolve an allowlisted app or directory, execute anything, or
journal anything. Those are Person 1's policy engine, runtime, and journal.
A valid plan from here is a *proposal* that has passed schema and registry
conformance - the second gate still runs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ..intent.normalize import NormalizedRequest, normalize_request
from ..intent.voice import VoiceIntake, VoiceOutcome
from ..providers.aws_polly import build_text_to_speech
from ..providers.aws_transcribe import build_speech_to_text
from ..providers.nine_router import build_chat_provider
from ..providers.settings import ProviderSettings
from .base import PlanOutcome, PlanOutcomeKind, Planner
from .contracts import contract_version, validator_info
from .digest import verify_digest
from .events import (
    clarification_required_event,
    events_for_outcome,
    provider_error_event,
    transcript_event,
)
from .fixture_planner import FixturePlanner
from .llm_planner import LlmPlanner
from .registry import ActionRegistry

logger = logging.getLogger("atlas.planner.service")


@dataclass(frozen=True)
class PlanningResult:
    """Everything the orchestrator needs from one planning request."""

    outcome: PlanOutcome
    request: NormalizedRequest
    #: Ordered :class:`AtlasEvent` envelopes, ready to emit as-is.
    events: list[dict[str, Any]] = field(default_factory=list)
    #: True when the plan came from the fixture planner or a mocked provider.
    mocked: bool = True

    @property
    def ok(self) -> bool:
        return self.outcome.ok

    @property
    def plan(self) -> dict[str, Any] | None:
        return self.outcome.plan

    @property
    def plan_digest(self) -> str:
        return self.outcome.plan_digest

    def describe(self) -> str:
        return f"{self.outcome.describe()} | {self.request.describe()} | events={len(self.events)}"


class PlanningService:
    """Composes normalization, the selected planner, and event construction."""

    def __init__(
        self,
        settings: ProviderSettings,
        *,
        planner: Planner | None = None,
        registry: ActionRegistry | None = None,
        speech_to_text: object | None = None,
        text_to_speech: object | None = None,
    ) -> None:
        self._settings = settings
        self._registry = registry or ActionRegistry.load()

        if planner is not None:
            self._planner: Planner = planner
            self._live = not bool(getattr(planner, "is_mock", False))
        elif settings.use_live_llm:
            chat_provider, _ = build_chat_provider(settings)
            self._planner = LlmPlanner(chat_provider, self._registry)
            self._live = True
        else:
            # Fail safe: no key, or mock mode on -> deterministic offline plans.
            self._planner = FixturePlanner(self._registry)
            self._live = False

        self._stt = speech_to_text if speech_to_text is not None else build_speech_to_text(settings)[0]
        self._tts = text_to_speech if text_to_speech is not None else build_text_to_speech(settings)[0]
        self._voice = VoiceIntake(self._stt)

        logger.info(
            "PlanningService ready: planner=%s live=%s actions=%s contract=%s validator=%s",
            getattr(self._planner, "name", "unknown"),
            self._live,
            list(self._registry.action_types),
            contract_version(),
            validator_info().backend,
        )

    # -- construction -----------------------------------------------------

    @classmethod
    def from_env(cls) -> "PlanningService":
        """Build from the process environment (``.env`` keys)."""
        return cls(ProviderSettings.from_env())

    @classmethod
    def for_mock_mode(cls) -> "PlanningService":
        """Explicitly offline service, for demos and Person 4's harness."""
        return cls(ProviderSettings(mock_mode=True))

    # -- introspection ----------------------------------------------------

    @property
    def mock_mode(self) -> bool:
        """True when no live LLM is in use. Drives the UI's offline badge."""
        return not self._live

    @property
    def planner_name(self) -> str:
        return str(getattr(self._planner, "name", "unknown"))

    @property
    def registry(self) -> ActionRegistry:
        return self._registry

    @property
    def voice_available(self) -> bool:
        return self._voice.available

    def diagnostics(self) -> dict[str, Any]:
        """Log-safe status for a settings/diagnostics screen and ``/health``.

        Contains no secret values - only presence flags - and states plainly
        which capabilities are mocked or not implemented, so the UI never
        implies a working integration (Master PRD FR-14, FR-13).
        """
        return {
            "planner": self.planner_name,
            "mockMode": self.mock_mode,
            "contractVersion": contract_version(),
            "actionRegistryVersion": self._registry.contract_version,
            "registeredActions": list(self._registry.action_types),
            "blockedCategories": [b.category for b in self._registry.blocked_categories],
            "schemaValidator": validator_info().describe(),
            "voice": {
                "provider": self._voice.provider_name,
                "available": self._voice.available,
                "mocked": self._voice.is_mock,
                "liveCallImplemented": bool(getattr(self._stt, "live_call_implemented", False)),
                "unavailableReason": self._voice.unavailable_reason,
            },
            "speech": {
                "provider": getattr(self._tts, "name", "unknown"),
                "available": bool(getattr(self._tts, "available", False)),
                "mocked": bool(getattr(self._tts, "is_mock", False)),
                "liveCallImplemented": bool(getattr(self._tts, "live_call_implemented", False)),
            },
            "providers": self._settings.describe(),
        }

    # -- planning ---------------------------------------------------------

    def plan_text(self, task_id: str, user_request: str | None) -> PlanningResult:
        """Plan a typed request (or an already-final transcript)."""
        normalized = normalize_request(user_request, source="text")
        return self._plan_normalized(task_id, normalized)

    def plan_voice(
        self,
        task_id: str,
        audio: bytes,
        *,
        sample_rate_hz: int = 16_000,
        language_code: str = "en-US",
    ) -> PlanningResult:
        """Transcribe then plan. Falls back to a clarification, never a failure.

        Master PRD FR-01: text stays usable when speech fails. A speech failure
        therefore produces ``provider_error`` *and* ``clarification_required``
        telling the user to type - it never fails the task.
        """
        voice: VoiceOutcome = self._voice.transcribe(
            audio, sample_rate_hz=sample_rate_hz, language_code=language_code
        )

        if not voice.ok:
            events: list[dict[str, Any]] = []
            if voice.error is not None:
                events.append(provider_error_event(task_id, voice.error))
            question = (
                voice.request.clarification_question
                if voice.request is not None and voice.request.clarification_question
                else voice.user_message()
            )
            events.append(clarification_required_event(task_id, question))
            fallback_request = voice.request or normalize_request("", source="voice")
            return PlanningResult(
                outcome=PlanOutcome.clarification(
                    question, mocked=voice.mocked or self.mock_mode, attempts=0
                ),
                request=fallback_request,
                events=events,
                mocked=voice.mocked or self.mock_mode,
            )

        assert voice.request is not None
        return self._plan_normalized(task_id, voice.request, provider_mocked=voice.mocked)

    def _plan_normalized(
        self,
        task_id: str,
        normalized: NormalizedRequest,
        *,
        provider_mocked: bool = False,
    ) -> PlanningResult:
        if not task_id:
            raise ValueError("task_id is required; the orchestrator owns task identity")

        events: list[dict[str, Any]] = []

        # Echo what we understood before anything else happens, so the UI can
        # show the request even if planning then fails (Master PRD 8.2 step 2).
        events.append(
            transcript_event(
                task_id,
                normalized.text,
                is_final=True,
                source=normalized.source,
            )
        )

        if normalized.suspicious:
            # Logged, not blocked: the request may be legitimate, and the real
            # defence is validating whatever the model returns. Recording it
            # gives Person 4 and the audit journal something to review.
            logger.warning(
                "Request carried injection-style markers (%s); planning continues under "
                "full validation",
                ",".join(normalized.injection_markers),
            )

        if normalized.needs_clarification:
            logger.info("Request needs clarification before planning: %s", normalized.describe())
            outcome = PlanOutcome.clarification(
                normalized.clarification_question,
                mocked=self.mock_mode,
            )
            events.extend(events_for_outcome(task_id, outcome))
            return PlanningResult(
                outcome=outcome,
                request=normalized,
                events=events,
                mocked=self.mock_mode,
            )

        logger.info("Planning with %s: %s", self.planner_name, normalized.describe())
        outcome = self._planner.plan(task_id, normalized.text)

        if outcome.mocked or provider_mocked:
            outcome = PlanOutcome(
                kind=outcome.kind,
                plan=outcome.plan,
                plan_digest=outcome.plan_digest,
                highest_risk_tier=outcome.highest_risk_tier,
                requires_confirmation=outcome.requires_confirmation,
                clarification_question=outcome.clarification_question,
                clarification_options=outcome.clarification_options,
                rejections=outcome.rejections,
                provider_error=outcome.provider_error,
                mocked=True,
                attempts=outcome.attempts,
                notes=outcome.notes,
            )

        events.extend(events_for_outcome(task_id, outcome))
        result = PlanningResult(
            outcome=outcome,
            request=normalized,
            events=events,
            mocked=outcome.mocked or self.mock_mode,
        )
        logger.info("Planning finished: %s", result.describe())
        return result

    # -- confirmation support ---------------------------------------------

    def verify_plan_digest(self, plan: dict[str, Any], submitted_digest: str) -> bool:
        """Check that a confirmation refers to the exact plan reviewed.

        Helper for the orchestrator's ``POST /confirm`` handler so the digest
        rule is implemented once. Enforcement (rejecting the request) stays
        with the API/orchestrator owner.
        """
        return verify_digest(plan, submitted_digest)

    # -- optional spoken feedback -----------------------------------------

    def speak(self, text: str) -> tuple[bytes | None, str]:
        """Best-effort spoken feedback. Returns ``(audio, reason_if_none)``.

        Never raises and never affects task status: speech is a convenience,
        and verified status always reaches the user through events.
        """
        synth = getattr(self._tts, "synthesize", None)
        if not callable(synth) or not getattr(self._tts, "available", False):
            return None, str(getattr(self._tts, "unavailable_reason", "") or "speech output unavailable")
        try:
            result = synth(text)
        except Exception as exc:  # defensive
            return None, f"speech provider raised {type(exc).__name__}"
        if not result.ok:
            assert result.error is not None
            return None, result.error.message or result.error.code.value
        return result.unwrap(), ""
