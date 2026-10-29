"""9Router-backed planner - parses untrusted model output into a validated plan.

OWNER: Person 2 - Voice, Intent & 9Router.

Pipeline, with the trust boundary marked:

    request -> constrained prompt -> 9Router -> raw text
                                                   |
             ====== untrusted below this line =====|
                                                   v
                               JSON extraction -> local validation
                                                   |
                               PLAN / CLARIFICATION / REJECTED

Guarantees this module is responsible for:

* **The model never owns identity.** ``taskId`` and ``userRequest`` are
  overwritten with the locally held values after parsing and before validation,
  so a model cannot smuggle different text into the plan the user reviews.
* **Bounded repair.** At most :data:`MAX_PLAN_ATTEMPTS` provider calls per
  request. The repair prompt is built only from *our* rejection messages, never
  from the model's previous reply, so malformed output is not re-injected.
* **No silent fix-ups.** If the final candidate still fails validation, the
  result is ``REJECTED`` with every rejection attached. Locally editing a plan
  to make it pass would hide a planning failure and would mean the user
  approves something the planner never proposed.
* **Provider failure is never task success.** A transport or gateway failure
  returns ``REJECTED`` carrying the typed :class:`ProviderError`
  (Master PRD 10.4).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from ..providers.base import ProviderError, ProviderErrorCode, ProviderResult
from .base import PlanOutcome
from .digest import compute_plan_digest
from .prompt import build_repair_prompt, build_system_prompt, build_user_prompt
from .registry import ActionRegistry
from .validation import PlanValidator, Rejection, RejectionCode, ValidationResult

logger = logging.getLogger("atlas.planner.llm")

#: Total provider calls per planning request, including repairs. Two is the
#: useful maximum: one honest attempt plus one corrected attempt. More spends
#: tokens (Master PRD 9.3) for diminishing returns - a model that cannot
#: satisfy an explicit error list on the second try rarely does on a third.
MAX_PLAN_ATTEMPTS = 2

#: Refuse to parse a reply larger than this, bounding JSON-scan cost on a
#: pathological response. The provider already caps the response body; this
#: caps what we are willing to interpret.
MAX_REPLY_CHARS = 100_000

#: Clarification text is shown verbatim in the UI, so it is length-bounded.
MAX_CLARIFICATION_CHARS = 300
MAX_CLARIFICATION_OPTIONS = 6

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


class LlmPlanner:
    """Planner backed by a ``ChatProvider`` (live 9Router or a scripted mock)."""

    name = "9router"

    def __init__(
        self,
        chat_provider: object,
        registry: ActionRegistry | None = None,
        validator: PlanValidator | None = None,
        *,
        max_attempts: int = MAX_PLAN_ATTEMPTS,
        mocked: bool | None = None,
    ) -> None:
        """
        ``mocked`` defaults to the provider's own ``is_mock`` flag, so a plan
        produced through a stub is always labelled as mocked without the caller
        having to remember to say so.
        """
        self._provider = chat_provider
        self._registry = registry or ActionRegistry.load()
        self._validator = validator or PlanValidator(self._registry)
        self._max_attempts = max(1, max_attempts)
        self._mocked = bool(getattr(chat_provider, "is_mock", False)) if mocked is None else mocked
        self._system_prompt = build_system_prompt(self._registry)

    @property
    def is_mock(self) -> bool:
        return self._mocked

    @property
    def system_prompt(self) -> str:
        """Exposed for tests and diagnostics. Contains no secrets."""
        return self._system_prompt

    # -- public -----------------------------------------------------------

    def plan(self, task_id: str, user_request: str) -> PlanOutcome:
        """Plan ``user_request``, repairing a rejected candidate once."""
        prompt = build_user_prompt(task_id, user_request)
        last_rejections: tuple[Rejection, ...] = ()

        for attempt in range(1, self._max_attempts + 1):
            result = self._complete(prompt)

            if not result.ok:
                assert result.error is not None
                # A provider failure ends planning immediately. Retrying the
                # transport is the provider's own job (bounded there); retrying
                # here would multiply the request count.
                logger.warning("Planning aborted on provider failure: %s", result.error.describe())
                return PlanOutcome.rejected(
                    provider_error=result.error,
                    mocked=self._mocked or result.mocked,
                    attempts=attempt,
                    notes=("no plan was produced; the provider call failed",),
                )

            raw_text = result.unwrap()
            parsed, parse_rejection = self._parse_reply(raw_text)

            if parse_rejection is not None:
                last_rejections = (parse_rejection,)
                logger.info(
                    "Attempt %d/%d: reply was not usable JSON (%s)",
                    attempt,
                    self._max_attempts,
                    parse_rejection.code.value,
                )
                if attempt < self._max_attempts:
                    prompt = build_repair_prompt(
                        task_id, user_request, [parse_rejection.message]
                    )
                    continue
                break

            assert parsed is not None

            clarification = self._as_clarification(parsed)
            if clarification is not None:
                question, options = clarification
                logger.info("Model asked for clarification instead of planning")
                return PlanOutcome.clarification(
                    question,
                    options=options,
                    mocked=self._mocked or result.mocked,
                    attempts=attempt,
                )

            # Local code owns identity. Overwrite before validating.
            candidate = dict(parsed)
            candidate["taskId"] = task_id
            candidate["userRequest"] = user_request

            validation = self._validator.validate(
                candidate,
                expected_task_id=task_id,
                expected_user_request=user_request,
            )

            if validation.valid:
                assert validation.plan is not None
                logger.info(
                    "Attempt %d/%d produced a valid plan (tier=%s, steps=%d)",
                    attempt,
                    self._max_attempts,
                    validation.highest_risk_tier,
                    len(validation.plan.get("steps", [])),
                )
                return PlanOutcome.from_validation(
                    validation,
                    digest=compute_plan_digest(validation.plan),
                    mocked=self._mocked or result.mocked,
                    attempts=attempt,
                )

            last_rejections = validation.rejections
            logger.info(
                "Attempt %d/%d rejected by local validation: %s",
                attempt,
                self._max_attempts,
                validation.describe(),
            )

            # A plan that reached for a blocked action or an unregistered one is
            # not a formatting mistake; re-prompting invites the model to try a
            # different way around the policy. Stop and report.
            if self._is_terminal(validation):
                return PlanOutcome.rejected(
                    rejections=validation.rejections,
                    mocked=self._mocked or result.mocked,
                    attempts=attempt,
                    notes=(
                        "the requested action is not permitted; "
                        "not retried to avoid prompting for a policy workaround",
                    ),
                )

            if attempt < self._max_attempts:
                prompt = build_repair_prompt(
                    task_id,
                    user_request,
                    [r.describe() for r in validation.rejections],
                )

        return PlanOutcome.rejected(
            rejections=last_rejections,
            mocked=self._mocked,
            attempts=self._max_attempts,
            notes=(
                f"no schema-valid plan after {self._max_attempts} attempt(s); "
                "nothing was executed",
            ),
        )

    # -- internals --------------------------------------------------------

    def _complete(self, user_prompt: str) -> ProviderResult[str]:
        """Call the provider, converting an unexpected raise into a typed error.

        The ``ChatProvider`` contract says implementations return errors rather
        than raising, but a planner must not fail open (or crash the task) if a
        provider breaks that contract.
        """
        try:
            return self._provider.complete(self._system_prompt, user_prompt)  # type: ignore[attr-defined]
        except Exception as exc:  # defensive
            logger.warning("Chat provider raised %s instead of returning a result", type(exc).__name__)
            return ProviderResult.failure(
                ProviderError(
                    provider="9router",
                    code=ProviderErrorCode.MALFORMED_RESPONSE,
                    message=f"Provider raised {type(exc).__name__}",
                    retryable=False,
                )
            )

    @staticmethod
    def _is_terminal(validation: ValidationResult) -> bool:
        """True for rejections that a retry must not attempt to talk around."""
        terminal = {
            RejectionCode.BLOCKED_ACTION,
            RejectionCode.UNKNOWN_ACTION,
            RejectionCode.RISK_TIER_BLOCKED,
            RejectionCode.SCHEMA_UNCHECKABLE,
        }
        return any(r.code in terminal for r in validation.rejections)

    def _parse_reply(self, raw: str) -> tuple[dict[str, Any] | None, Rejection | None]:
        """Extract a single JSON object from the reply text.

        Handles the three shapes real gateways produce: bare JSON, JSON inside
        a ``​```json`` fence, and JSON preceded or followed by prose. Anything
        else is a rejection, not a best-effort salvage.
        """
        if len(raw) > MAX_REPLY_CHARS:
            return None, Rejection(
                code=RejectionCode.SCHEMA_INVALID,
                message=f"reply of {len(raw)} chars exceeds the {MAX_REPLY_CHARS}-char parse ceiling",
            )

        text = raw.strip()
        fenced = _FENCE.search(text)
        if fenced:
            text = fenced.group(1).strip()

        candidates = [text]
        extracted = _extract_first_object(text)
        if extracted is not None and extracted != text:
            candidates.append(extracted)

        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed, None
            return None, Rejection(
                code=RejectionCode.NOT_AN_OBJECT,
                message=f"reply JSON was a {type(parsed).__name__}, expected a single object",
            )

        return None, Rejection(
            code=RejectionCode.SCHEMA_INVALID,
            message="reply did not contain a parseable JSON object; reply with JSON only",
        )

    @staticmethod
    def _as_clarification(parsed: dict[str, Any]) -> tuple[str, tuple[str, ...]] | None:
        """Return ``(question, options)`` if the reply is a clarification.

        A reply carrying ``steps`` is treated as a plan attempt even if it also
        carries ``clarification``: the safe reading of an ambiguous reply is the
        one that gets validated, not the one that bypasses validation.
        """
        if "steps" in parsed:
            return None
        question = parsed.get("clarification") or parsed.get("question")
        if not isinstance(question, str) or not question.strip():
            return None

        text = question.strip()[:MAX_CLARIFICATION_CHARS]
        raw_options = parsed.get("options")
        options: tuple[str, ...] = ()
        if isinstance(raw_options, list):
            options = tuple(
                option.strip()[:MAX_CLARIFICATION_CHARS]
                for option in raw_options[:MAX_CLARIFICATION_OPTIONS]
                if isinstance(option, str) and option.strip()
            )
        return text, options


def _extract_first_object(text: str) -> str | None:
    """Return the first balanced ``{...}`` span, ignoring braces inside strings."""
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None
