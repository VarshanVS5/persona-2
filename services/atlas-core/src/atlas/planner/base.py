"""The planner interface Person 1 and the orchestrator consume.

OWNER: Person 2 - Voice, Intent & 9Router.

This is the stable seam promised in ``INSTRUCTIONS.md`` section 5 ("Provides
to: Person 1 & orchestrator: schema-valid ``TaskPlan`` (planner interface) and
a deterministic fixture planner").

One rule shapes the whole type: :class:`PlanOutcome` has exactly three shapes -
``PLAN``, ``CLARIFICATION``, ``REJECTED`` - and a ``PLAN`` outcome is only ever
constructed from a plan that passed :class:`atlas.planner.validation.
PlanValidator`. There is no code path that yields an unvalidated plan, so a
consumer cannot accidentally receive raw model output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from ..providers.base import ProviderError
from .validation import Rejection, ValidationResult


class PlanOutcomeKind(str, Enum):
    """Which of the three planner results occurred."""

    #: A validated, schema-conformant plan is ready for review.
    PLAN = "PLAN"
    #: The request was ambiguous or unsupported; ask the user a question.
    CLARIFICATION = "CLARIFICATION"
    #: No usable plan could be produced (provider failure or invalid output).
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class PlanOutcome:
    """Typed planner result."""

    kind: PlanOutcomeKind
    plan: dict[str, Any] | None = None
    plan_digest: str = ""
    highest_risk_tier: str = "T0"
    requires_confirmation: bool = True
    clarification_question: str = ""
    clarification_options: tuple[str, ...] = ()
    rejections: tuple[Rejection, ...] = ()
    provider_error: ProviderError | None = None
    #: True when the plan came from a fixture/mock rather than a live provider.
    mocked: bool = True
    #: Provider attempts spent, for diagnostics and budget accounting.
    attempts: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.kind is PlanOutcomeKind.PLAN

    def describe(self) -> str:
        if self.kind is PlanOutcomeKind.PLAN:
            return (
                f"PLAN steps={len(self.plan.get('steps', [])) if self.plan else 0} "
                f"tier={self.highest_risk_tier} confirm={self.requires_confirmation} "
                f"mocked={self.mocked} digest={self.plan_digest[:19]}..."
            )
        if self.kind is PlanOutcomeKind.CLARIFICATION:
            return f"CLARIFICATION {self.clarification_question!r}"
        reasons = "; ".join(r.describe() for r in self.rejections)
        if self.provider_error is not None:
            reasons = f"{self.provider_error.describe()}{'; ' + reasons if reasons else ''}"
        return f"REJECTED {reasons}"

    # -- constructors -----------------------------------------------------

    @classmethod
    def from_validation(
        cls,
        result: ValidationResult,
        *,
        digest: str,
        mocked: bool,
        attempts: int,
        notes: tuple[str, ...] = (),
    ) -> "PlanOutcome":
        """Build a ``PLAN`` outcome from a *passed* validation result."""
        if not result.valid or result.plan is None:
            raise ValueError("from_validation requires a valid ValidationResult")
        return cls(
            kind=PlanOutcomeKind.PLAN,
            plan=result.plan,
            plan_digest=digest,
            highest_risk_tier=result.highest_risk_tier,
            requires_confirmation=result.requires_confirmation,
            mocked=mocked,
            attempts=attempts,
            notes=notes,
        )

    @classmethod
    def clarification(
        cls,
        question: str,
        *,
        options: tuple[str, ...] = (),
        mocked: bool = True,
        attempts: int = 0,
        notes: tuple[str, ...] = (),
    ) -> "PlanOutcome":
        return cls(
            kind=PlanOutcomeKind.CLARIFICATION,
            clarification_question=question,
            clarification_options=options,
            mocked=mocked,
            attempts=attempts,
            notes=notes,
        )

    @classmethod
    def rejected(
        cls,
        *,
        rejections: tuple[Rejection, ...] = (),
        provider_error: ProviderError | None = None,
        mocked: bool = True,
        attempts: int = 0,
        notes: tuple[str, ...] = (),
    ) -> "PlanOutcome":
        return cls(
            kind=PlanOutcomeKind.REJECTED,
            rejections=rejections,
            provider_error=provider_error,
            mocked=mocked,
            attempts=attempts,
            notes=notes,
        )


@runtime_checkable
class Planner(Protocol):
    """What the orchestrator depends on. Implemented by both planners."""

    name: str

    def plan(self, task_id: str, user_request: str) -> PlanOutcome:
        """Return a validated plan, a clarification, or a rejection.

        Implementations must not raise for provider or model failures; those
        are returned as ``REJECTED``.
        """
