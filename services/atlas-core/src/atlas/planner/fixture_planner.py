"""Deterministic fixture planner - the offline/mock-mode planning path.

OWNER: Person 2 - Voice, Intent & 9Router.

``INSTRUCTIONS.md`` section 6: "start with a deterministic fixture planner and
provider mocks (``ATLAS_MOCK_MODE=true``)". This is also what Person 4's
end-to-end mock workflow and the demo run against.

Three properties make it safe to ship alongside the live path:

* **It builds plans from the registry, not from strings.** Every step's risk
  tier, timeout, and condition types come from
  ``packages/contracts/action-registry.json``, so a fixture plan cannot drift
  out of conformance when the registry changes.
* **It goes through exactly the same validator as the live planner.** A
  fixture plan is not trusted because it is local; it is validated. A bug here
  surfaces as a rejection, not as an unchecked plan.
* **It is labelled.** Every outcome carries ``mocked=True``, which
  :mod:`atlas.planner.service` propagates into events so the UI can show
  offline mode rather than implying a live LLM produced the plan.

Matching is intentionally shallow keyword matching, not a second NLU
implementation: an unrecognised request returns a clarification rather than a
guessed plan (Master PRD FR-04).
"""

from __future__ import annotations

import logging
import re

from .base import PlanOutcome, Planner
from .digest import compute_plan_digest
from .registry import ActionRegistry
from .validation import PlanValidator, Rejection, RejectionCode

logger = logging.getLogger("atlas.planner.fixture")

#: Keyword -> appId candidates for ``app.open``. ``appId`` must appear in
#: ``ATLAS_ALLOWED_APPS``; the *authoritative* allowlist check is Person 1's
#: policy engine, so this map only proposes a candidate.
_APP_KEYWORDS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b(notepad|note pad|text editor|editor|notes)\b"), "notepad"),
    (re.compile(r"(?i)\b(wordpad)\b"), "wordpad"),
    (re.compile(r"(?i)\b(vs ?code|visual studio code|code editor)\b"), "code"),
)

_OPEN_VERBS = re.compile(r"(?i)\b(open|launch|start|run|bring up|show)\b")


class FixturePlanner:
    """Registry-driven offline planner. Satisfies :class:`Planner`."""

    name = "fixture"
    is_mock = True

    def __init__(
        self,
        registry: ActionRegistry | None = None,
        validator: PlanValidator | None = None,
        *,
        default_app_id: str = "notepad",
    ) -> None:
        self._registry = registry or ActionRegistry.load()
        self._validator = validator or PlanValidator(self._registry)
        self._default_app_id = default_app_id

    def plan(self, task_id: str, user_request: str) -> PlanOutcome:
        """Produce a validated fixture plan, or a clarification."""
        candidate = self._match(task_id, user_request)
        if candidate is None:
            logger.info("Fixture planner found no supported action for the request")
            return PlanOutcome.clarification(
                self._clarification_question(),
                options=self._clarification_options(),
                mocked=True,
                notes=("offline fixture planner: no keyword match",),
            )

        result = self._validator.validate(
            candidate,
            expected_task_id=task_id,
            expected_user_request=user_request,
        )
        if not result.valid:
            # A fixture plan failing validation is a bug in *this* module or a
            # contract change. Report it as a rejection; never bypass the gate.
            logger.error("Fixture plan failed local validation: %s", result.describe())
            return PlanOutcome.rejected(
                rejections=result.rejections
                + (
                    Rejection(
                        code=RejectionCode.SCHEMA_INVALID,
                        message=(
                            "the offline fixture planner produced a non-conformant plan; "
                            "this is an ATLAS defect, not a user error"
                        ),
                    ),
                ),
                mocked=True,
            )

        assert result.plan is not None
        return PlanOutcome.from_validation(
            result,
            digest=compute_plan_digest(result.plan),
            mocked=True,
            attempts=0,
            notes=("plan produced offline by the deterministic fixture planner",),
        )

    # -- matching ---------------------------------------------------------

    def _match(self, task_id: str, user_request: str) -> dict[str, object] | None:
        """Return a candidate plan dict, or None when nothing matches."""
        if not self._registry.is_registered("app.open"):
            # The registry is the authority: if app.open is gone, this planner
            # has nothing to offer rather than something to improvise.
            return None

        app_id = self._match_app_id(user_request)
        if app_id is None:
            return None
        if not _OPEN_VERBS.search(user_request):
            # "notepad" alone is not a command. Asking beats assuming.
            return None

        return self._build_app_open_plan(task_id, user_request, app_id)

    def _match_app_id(self, user_request: str) -> str | None:
        for pattern, app_id in _APP_KEYWORDS:
            if pattern.search(user_request):
                return app_id
        return None

    def _build_app_open_plan(self, task_id: str, user_request: str, app_id: str) -> dict[str, object]:
        """Build an ``app.open`` plan entirely from registry metadata."""
        definition = self._registry.get("app.open")
        assert definition is not None  # checked by caller

        return {
            "taskId": task_id,
            "userRequest": user_request,
            "summary": f"Open the allowlisted application '{app_id}'.",
            # Local code sets this, derived from the registry tier. It is never
            # lowered, and the validator re-checks it.
            "requiresConfirmation": True,
            "steps": [
                {
                    "stepId": 1,
                    "actionType": definition.action_type,
                    "arguments": {"appId": app_id},
                    "riskTier": definition.risk_tier,
                    "preconditions": [
                        {"type": condition_type, "args": {"appId": app_id}}
                        for condition_type in sorted(definition.precondition_types)
                    ],
                    "postconditions": [
                        {"type": condition_type, "args": {"appId": app_id}}
                        for condition_type in sorted(definition.postcondition_types)
                    ],
                    "timeoutMs": definition.timeout_ms,
                    "idempotencyKey": f"{task_id}-step-1",
                }
            ],
        }

    # -- clarifications ---------------------------------------------------

    def _clarification_question(self) -> str:
        return (
            "I can only perform a short list of actions in this build. "
            "Which of these would you like?"
        )

    def _clarification_options(self) -> tuple[str, ...]:
        options = [
            f"{action.action_type} - {action.description}" for action in self._registry.actions
        ]
        return tuple(options)


def build_fixture_planner(registry: ActionRegistry | None = None) -> Planner:
    """Convenience constructor used by :mod:`atlas.planner.service`."""
    return FixturePlanner(registry)
