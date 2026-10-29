"""Plan validation - the local gate that untrusted model output must pass.

OWNER: Person 2 - Voice, Intent & 9Router.

Master PRD FR-02: "LLM output is untrusted and must be validated locally.
Natural-language model output must never be executed directly."
Master PRD 7: "The risk tier is assigned and enforced by local code. The model
cannot lower a tier or grant itself permission."
Master PRD 8.3: "The LLM is a planner, not an authority."

This module turns those sentences into executable rules. Everything it enforces
is derived from ``packages/contracts/`` - no action type, tier, or timeout is
hardcoded here.

Layered checks, in order (later layers assume earlier ones passed):

1. **Shape** - must be a JSON object, with a bounded number of steps.
2. **Schema** - ``task-plan.schema.json``, including ``additionalProperties:
   false``, so an unknown field cannot smuggle instructions through.
3. **Identity** - ``taskId``/``userRequest`` must match the locally held
   values. The model does not get to choose the task identity.
4. **Registry conformance** per step - blocked category, unknown action,
   argument schema, risk tier equality, timeout ceiling, required condition
   types.
5. **Plan-level invariants** - step numbering, unique idempotency keys,
   confirmation not lowered, no blocked tier.

Relationship to Person 1: passing validation here means the plan is
*well-formed and registry-conformant*. It does **not** mean it is authorized.
Authorization (allowlisted app/dir resolution, consent state, execution) is
Person 1's policy engine and runtime. Both gates run; this one is first.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .contracts import validate_against_schema, validate_task_plan_schema
from .jsonschema_lite import SchemaViolation, UnsupportedSchemaFeature
from .registry import (
    BLOCKED_RISK_TIERS,
    CONFIRMATION_REQUIRED_FROM_TIER,
    ActionRegistry,
    highest_risk_tier,
    risk_at_least,
)

#: Hard ceiling on plan size. A single spoken command does not legitimately
#: expand into dozens of desktop actions; a plan that large is either a model
#: failure or an attempt to bury a harmful step in noise the user will not read.
MAX_STEPS = 10

#: Ceiling on the plan's free-text fields, so an oversized ``summary`` cannot
#: be used to flood the UI, the journal, or an event payload.
MAX_SUMMARY_CHARS = 600
MAX_USER_REQUEST_CHARS = 2_000


class RejectionCode(str, Enum):
    """Closed taxonomy of plan rejections.

    Closed on purpose: Person 1, Person 3, and Person 4 all branch on these, so
    a new failure mode should appear as a new named code rather than as a
    different free-text message.
    """

    NOT_AN_OBJECT = "NOT_AN_OBJECT"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    SCHEMA_UNCHECKABLE = "SCHEMA_UNCHECKABLE"
    EMPTY_PLAN = "EMPTY_PLAN"
    PLAN_TOO_LARGE = "PLAN_TOO_LARGE"
    SUMMARY_TOO_LONG = "SUMMARY_TOO_LONG"
    USER_REQUEST_TOO_LONG = "USER_REQUEST_TOO_LONG"
    TASK_ID_MISMATCH = "TASK_ID_MISMATCH"
    USER_REQUEST_MISMATCH = "USER_REQUEST_MISMATCH"
    BLOCKED_ACTION = "BLOCKED_ACTION"
    UNKNOWN_ACTION = "UNKNOWN_ACTION"
    INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
    RISK_TIER_MISMATCH = "RISK_TIER_MISMATCH"
    RISK_TIER_BLOCKED = "RISK_TIER_BLOCKED"
    TIMEOUT_OUT_OF_BOUNDS = "TIMEOUT_OUT_OF_BOUNDS"
    MISSING_PRECONDITION = "MISSING_PRECONDITION"
    MISSING_POSTCONDITION = "MISSING_POSTCONDITION"
    STEP_SEQUENCE_INVALID = "STEP_SEQUENCE_INVALID"
    DUPLICATE_IDEMPOTENCY_KEY = "DUPLICATE_IDEMPOTENCY_KEY"
    CONFIRMATION_LOWERED = "CONFIRMATION_LOWERED"


@dataclass(frozen=True)
class Rejection:
    """One reason a plan was refused, addressed to a specific step when known."""

    code: RejectionCode
    message: str
    step_id: int | None = None
    path: str = ""

    def describe(self) -> str:
        where = f"step {self.step_id}" if self.step_id is not None else (self.path or "plan")
        return f"[{self.code.value}] {where}: {self.message}"

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = {"code": self.code.value, "message": self.message}
        if self.step_id is not None:
            out["stepId"] = self.step_id
        if self.path:
            out["path"] = self.path
        return out


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of validating one candidate plan."""

    valid: bool
    plan: dict[str, Any] | None = None
    rejections: tuple[Rejection, ...] = ()
    highest_risk_tier: str = "T0"
    requires_confirmation: bool = True

    @property
    def codes(self) -> tuple[RejectionCode, ...]:
        return tuple(r.code for r in self.rejections)

    def has(self, code: RejectionCode) -> bool:
        return any(r.code == code for r in self.rejections)

    def describe(self) -> str:
        if self.valid:
            return f"valid plan (highestRiskTier={self.highest_risk_tier})"
        return "; ".join(r.describe() for r in self.rejections)

    def to_dict(self) -> dict[str, object]:
        """Serializable summary for events, journals, and defect reports."""
        return {
            "valid": self.valid,
            "highestRiskTier": self.highest_risk_tier,
            "requiresConfirmation": self.requires_confirmation,
            "rejections": [r.to_dict() for r in self.rejections],
        }


@dataclass
class _Collector:
    rejections: list[Rejection] = field(default_factory=list)

    def add(
        self,
        code: RejectionCode,
        message: str,
        *,
        step_id: int | None = None,
        path: str = "",
    ) -> None:
        self.rejections.append(Rejection(code=code, message=message, step_id=step_id, path=path))


class PlanValidator:
    """Validates candidate plans against the schema and the action registry."""

    def __init__(self, registry: ActionRegistry | None = None) -> None:
        self._registry = registry or ActionRegistry.load()

    @property
    def registry(self) -> ActionRegistry:
        return self._registry

    def validate(
        self,
        candidate: Any,
        *,
        expected_task_id: str | None = None,
        expected_user_request: str | None = None,
    ) -> ValidationResult:
        """Validate ``candidate`` and return every problem found.

        All rejections are collected rather than failing on the first, because
        a planner repair prompt and a defect report are both far more useful
        with the complete list.
        """
        collector = _Collector()

        if not isinstance(candidate, dict):
            collector.add(
                RejectionCode.NOT_AN_OBJECT,
                f"plan must be a JSON object, got {type(candidate).__name__}",
            )
            return self._result(None, collector)

        # -- schema ---------------------------------------------------------
        try:
            violations = validate_task_plan_schema(candidate)
        except UnsupportedSchemaFeature as exc:
            # The contract outgrew the built-in validator. Refuse rather than
            # pass an unchecked plan through to the runtime.
            collector.add(
                RejectionCode.SCHEMA_UNCHECKABLE,
                f"task-plan schema cannot be enforced locally: {exc}",
            )
            return self._result(None, collector)

        for violation in violations:
            collector.add(
                RejectionCode.SCHEMA_INVALID,
                violation.message,
                step_id=_step_id_from_path(violation.path, candidate),
                path=violation.path,
            )
        if violations:
            # Downstream checks index into fields the schema just rejected;
            # continuing would produce misleading secondary errors.
            return self._result(None, collector)

        # -- identity (the model does not own these fields) -----------------
        if expected_task_id is not None and candidate.get("taskId") != expected_task_id:
            collector.add(
                RejectionCode.TASK_ID_MISMATCH,
                f"taskId must be {expected_task_id!r}, plan carries {candidate.get('taskId')!r}",
                path="/taskId",
            )
        if expected_user_request is not None and candidate.get("userRequest") != expected_user_request:
            collector.add(
                RejectionCode.USER_REQUEST_MISMATCH,
                "userRequest does not match the request the user actually submitted",
                path="/userRequest",
            )

        # -- bounded free text ----------------------------------------------
        summary = candidate.get("summary", "")
        if isinstance(summary, str) and len(summary) > MAX_SUMMARY_CHARS:
            collector.add(
                RejectionCode.SUMMARY_TOO_LONG,
                f"summary is {len(summary)} chars, ceiling is {MAX_SUMMARY_CHARS}",
                path="/summary",
            )
        user_request = candidate.get("userRequest", "")
        if isinstance(user_request, str) and len(user_request) > MAX_USER_REQUEST_CHARS:
            collector.add(
                RejectionCode.USER_REQUEST_TOO_LONG,
                f"userRequest is {len(user_request)} chars, ceiling is {MAX_USER_REQUEST_CHARS}",
                path="/userRequest",
            )

        # -- steps ----------------------------------------------------------
        steps = candidate.get("steps")
        steps = steps if isinstance(steps, list) else []
        if not steps:
            collector.add(
                RejectionCode.EMPTY_PLAN,
                "plan contains no steps; ask the user for clarification instead",
                path="/steps",
            )
            return self._result(None, collector)
        if len(steps) > MAX_STEPS:
            collector.add(
                RejectionCode.PLAN_TOO_LARGE,
                f"plan has {len(steps)} steps, ceiling is {MAX_STEPS}",
                path="/steps",
            )
            return self._result(None, collector)

        tiers: list[str] = []
        seen_keys: dict[str, int] = {}

        for index, step in enumerate(steps):
            step_id = step.get("stepId") if isinstance(step, dict) else None
            path = f"/steps/{index}"

            if not isinstance(step_id, int) or isinstance(step_id, bool) or step_id != index + 1:
                collector.add(
                    RejectionCode.STEP_SEQUENCE_INVALID,
                    f"stepId must be {index + 1} (steps are 1-based and contiguous), got {step_id!r}",
                    path=f"{path}/stepId",
                )

            key = step.get("idempotencyKey")
            if isinstance(key, str):
                if key in seen_keys:
                    collector.add(
                        RejectionCode.DUPLICATE_IDEMPOTENCY_KEY,
                        f"idempotencyKey {key!r} already used by step {seen_keys[key]}; "
                        "duplicate keys make retry-safety undecidable",
                        step_id=step_id if isinstance(step_id, int) else None,
                        path=f"{path}/idempotencyKey",
                    )
                else:
                    seen_keys[key] = step_id if isinstance(step_id, int) else index + 1

            tiers.append(self._validate_step(step, collector, path=path))

        # -- plan-level invariants ------------------------------------------
        top_tier = highest_risk_tier(tiers)

        for tier in sorted(set(tiers)):
            if tier in BLOCKED_RISK_TIERS:
                collector.add(
                    RejectionCode.RISK_TIER_BLOCKED,
                    f"risk tier {tier} is blocked in the prototype "
                    "(no explicit authorization flow exists for it)",
                )

        requires_confirmation = candidate.get("requiresConfirmation")
        must_confirm = risk_at_least(top_tier, CONFIRMATION_REQUIRED_FROM_TIER)
        if must_confirm and requires_confirmation is not True:
            collector.add(
                RejectionCode.CONFIRMATION_LOWERED,
                f"requiresConfirmation must be true for a plan whose highest risk tier is "
                f"{top_tier} (threshold {CONFIRMATION_REQUIRED_FROM_TIER}); "
                "the model cannot waive user consent",
                path="/requiresConfirmation",
            )

        if collector.rejections:
            return self._result(None, collector, top_tier=top_tier)

        return ValidationResult(
            valid=True,
            plan=candidate,
            rejections=(),
            highest_risk_tier=top_tier,
            # Local code decides the effective consent requirement. Even if a
            # plan says false below the threshold, we never *lower* it.
            requires_confirmation=bool(requires_confirmation) or must_confirm,
        )

    # -- per-step ---------------------------------------------------------

    def _validate_step(self, step: dict[str, Any], collector: _Collector, *, path: str) -> str:
        """Validate one step; returns the *registry* tier for risk accounting.

        The registry tier is returned rather than the plan's declared tier, so
        a model that writes ``T0`` on a ``T2`` action cannot influence
        plan-level confirmation accounting even while that mismatch is being
        reported.
        """
        step_id = step.get("stepId") if isinstance(step.get("stepId"), int) else None
        action_type = step.get("actionType")
        declared_tier = step.get("riskTier", "T0")

        if not isinstance(action_type, str) or not action_type:
            collector.add(
                RejectionCode.UNKNOWN_ACTION,
                "actionType is missing or not a string",
                step_id=step_id,
                path=f"{path}/actionType",
            )
            return declared_tier if isinstance(declared_tier, str) else "T0"

        # Blocked categories are checked first: "shell.exec" is both blocked
        # and unregistered, and "blocked" is the message the user needs.
        blocked = self._registry.blocked_match(action_type)
        if blocked is not None:
            collector.add(
                RejectionCode.BLOCKED_ACTION,
                f"action {action_type!r} falls under blocked category "
                f"{blocked.category!r}: {blocked.reason}",
                step_id=step_id,
                path=f"{path}/actionType",
            )
            return "T3"  # treat a blocked action as maximum severity

        definition = self._registry.get(action_type)
        if definition is None:
            collector.add(
                RejectionCode.UNKNOWN_ACTION,
                f"action {action_type!r} is not in action-registry.json; "
                f"registered actions are {list(self._registry.action_types)}",
                step_id=step_id,
                path=f"{path}/actionType",
            )
            return "T3"  # unknown means unauthorized, not harmless

        # -- arguments ------------------------------------------------------
        arguments = step.get("arguments")
        if definition.arguments_schema:
            try:
                arg_violations = validate_against_schema(arguments, definition.arguments_schema)
            except UnsupportedSchemaFeature as exc:
                collector.add(
                    RejectionCode.SCHEMA_UNCHECKABLE,
                    f"argumentsSchema for {action_type!r} cannot be enforced locally: {exc}",
                    step_id=step_id,
                    path=f"{path}/arguments",
                )
                arg_violations = []
            for violation in arg_violations:
                collector.add(
                    RejectionCode.INVALID_ARGUMENTS,
                    f"arguments for {action_type!r}: {violation.message}",
                    step_id=step_id,
                    path=f"{path}/arguments{violation.path}",
                )

        # -- risk tier ------------------------------------------------------
        if declared_tier != definition.risk_tier:
            collector.add(
                RejectionCode.RISK_TIER_MISMATCH,
                f"riskTier for {action_type!r} is fixed at {definition.risk_tier} by the registry, "
                f"plan declares {declared_tier!r}; the model cannot reassign a tier",
                step_id=step_id,
                path=f"{path}/riskTier",
            )

        # -- timeout --------------------------------------------------------
        timeout_ms = step.get("timeoutMs")
        if isinstance(timeout_ms, int) and not isinstance(timeout_ms, bool):
            if timeout_ms > definition.timeout_ms:
                collector.add(
                    RejectionCode.TIMEOUT_OUT_OF_BOUNDS,
                    f"timeoutMs {timeout_ms} exceeds the registry ceiling "
                    f"{definition.timeout_ms} for {action_type!r}",
                    step_id=step_id,
                    path=f"{path}/timeoutMs",
                )

        # -- conditions -----------------------------------------------------
        # Only condition *types* are compared. The registry stores templates
        # (``{"argument": "appId"}``) while a plan carries materialised args
        # (``{"appId": "notepad"}``) - see examples/plan.valid.json - so
        # comparing args here would reject the canonical valid example.
        # Evaluating condition arguments against real system state is the
        # verifier's job (Person 1).
        plan_pre = _condition_types(step.get("preconditions"))
        plan_post = _condition_types(step.get("postconditions"))

        for missing in sorted(definition.precondition_types - plan_pre):
            collector.add(
                RejectionCode.MISSING_PRECONDITION,
                f"{action_type!r} requires precondition type {missing!r}",
                step_id=step_id,
                path=f"{path}/preconditions",
            )
        for missing in sorted(definition.postcondition_types - plan_post):
            collector.add(
                RejectionCode.MISSING_POSTCONDITION,
                f"{action_type!r} requires postcondition type {missing!r}; "
                "without it the result cannot be verified",
                step_id=step_id,
                path=f"{path}/postconditions",
            )

        return definition.risk_tier

    @staticmethod
    def _result(
        plan: dict[str, Any] | None,
        collector: _Collector,
        *,
        top_tier: str = "T0",
    ) -> ValidationResult:
        return ValidationResult(
            valid=False,
            plan=plan,
            rejections=tuple(collector.rejections),
            highest_risk_tier=top_tier,
            # A rejected plan never runs, but if anything were to read this
            # field it must read "consent required", not "consent waived".
            requires_confirmation=True,
        )


def _condition_types(conditions: Any) -> frozenset[str]:
    if not isinstance(conditions, list):
        return frozenset()
    return frozenset(
        str(c.get("type"))
        for c in conditions
        if isinstance(c, dict) and isinstance(c.get("type"), str) and c.get("type")
    )


def _step_id_from_path(path: str, candidate: dict[str, Any]) -> int | None:
    """Best-effort mapping of a schema violation path to a 1-based step id."""
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 2 and parts[0] == "steps" and parts[1].isdigit():
        return int(parts[1]) + 1
    return None


def summarize_violations(violations: list[SchemaViolation]) -> str:
    """Join schema violations into one log-safe line."""
    return "; ".join(str(v) for v in violations)
