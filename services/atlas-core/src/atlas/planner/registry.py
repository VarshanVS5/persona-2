"""Typed view over ``packages/contracts/action-registry.json``.

OWNER: Person 2 - Voice, Intent & 9Router.

Master PRD FR-03: every executable action is registered with a documented
schema, allowed arguments, risk tier, preconditions, postconditions, timeout,
and failure behaviour; unknown action types must be rejected.

This module is read-only over a project-owner-owned file. Person 2 may not
invent action types or permissions (``INSTRUCTIONS.md`` section 4), so the
planner derives *everything* - the prompt's action catalogue, the validator's
allowlist, and the risk tier of each step - from this one source. Adding an
action is a PR from Person 1 to the project owner, after which the planner and
prompt pick it up with no code change here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .contracts import ContractsUnavailable, load_action_registry

#: Risk tiers in ascending severity (Master PRD 7). Order is what lets us say
#: "the model may not *lower* a tier" and "T3 is blocked in the prototype".
RISK_TIERS: tuple[str, ...] = ("T0", "T1", "T2", "T3")
RISK_ORDER: Mapping[str, int] = {tier: index for index, tier in enumerate(RISK_TIERS)}

#: Tiers that may never appear in a prototype plan (Master PRD 7: T3 is
#: "Block in prototype or require a separately designed, explicit
#: authorization flow"; no such flow exists, so T3 is blocked).
BLOCKED_RISK_TIERS = frozenset({"T3"})

#: Lowest tier that forces ``requiresConfirmation: true``. Set to T1 because
#: the single registered action (``app.open``, T1) ships with
#: ``requiresConfirmation: true`` in ``examples/plan.valid.json``, and because
#: erring toward asking the user is the fail-safe direction. Raising this to T2
#: (strict Master PRD 7 reading) would be a deliberate relaxation and needs the
#: project owner's sign-off.
CONFIRMATION_REQUIRED_FROM_TIER = "T1"


@dataclass(frozen=True)
class ActionDefinition:
    """One registered, executable action."""

    action_type: str
    description: str
    risk_tier: str
    arguments_schema: dict[str, Any]
    preconditions: tuple[dict[str, Any], ...]
    postconditions: tuple[dict[str, Any], ...]
    timeout_ms: int
    on_failure: str

    @property
    def required_arguments(self) -> tuple[str, ...]:
        required = self.arguments_schema.get("required")
        return tuple(str(key) for key in required) if isinstance(required, list) else ()

    @property
    def allowed_arguments(self) -> tuple[str, ...]:
        properties = self.arguments_schema.get("properties")
        return tuple(properties.keys()) if isinstance(properties, dict) else ()

    @property
    def precondition_types(self) -> frozenset[str]:
        return frozenset(str(c.get("type")) for c in self.preconditions if c.get("type"))

    @property
    def postcondition_types(self) -> frozenset[str]:
        return frozenset(str(c.get("type")) for c in self.postconditions if c.get("type"))


@dataclass(frozen=True)
class BlockedCategory:
    """A category of action that must never be planned (Master PRD 7)."""

    category: str
    reason: str


class ActionRegistry:
    """Immutable, validated view of the shared action registry."""

    def __init__(
        self,
        actions: Mapping[str, ActionDefinition],
        blocked: tuple[BlockedCategory, ...],
        contract_version: str,
    ) -> None:
        self._actions = dict(actions)
        self._blocked = blocked
        self.contract_version = contract_version

    # -- construction -----------------------------------------------------

    @classmethod
    def load(cls) -> "ActionRegistry":
        """Load and structurally validate the registry file."""
        raw = load_action_registry()
        raw_actions = raw.get("actions")
        if not isinstance(raw_actions, list) or not raw_actions:
            raise ContractsUnavailable(
                "action-registry.json must contain a non-empty 'actions' array; "
                "refusing to plan with an empty allowlist"
            )

        actions: dict[str, ActionDefinition] = {}
        for index, entry in enumerate(raw_actions):
            if not isinstance(entry, dict):
                raise ContractsUnavailable(f"actions[{index}] is not an object")
            action_type = entry.get("actionType")
            risk_tier = entry.get("riskTier")
            timeout_ms = entry.get("timeoutMs")
            if not isinstance(action_type, str) or not action_type:
                raise ContractsUnavailable(f"actions[{index}] has no actionType")
            if risk_tier not in RISK_ORDER:
                raise ContractsUnavailable(
                    f"action {action_type!r} has riskTier {risk_tier!r}, expected one of {RISK_TIERS}"
                )
            if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or timeout_ms < 1:
                raise ContractsUnavailable(
                    f"action {action_type!r} has a non-positive integer timeoutMs: {timeout_ms!r}"
                )
            if action_type in actions:
                raise ContractsUnavailable(f"duplicate action {action_type!r} in registry")

            arguments_schema = entry.get("argumentsSchema")
            actions[action_type] = ActionDefinition(
                action_type=action_type,
                description=str(entry.get("description") or ""),
                risk_tier=risk_tier,
                arguments_schema=arguments_schema if isinstance(arguments_schema, dict) else {},
                preconditions=tuple(
                    c for c in (entry.get("preconditions") or []) if isinstance(c, dict)
                ),
                postconditions=tuple(
                    c for c in (entry.get("postconditions") or []) if isinstance(c, dict)
                ),
                timeout_ms=timeout_ms,
                on_failure=str(entry.get("onFailure") or "stop_and_report"),
            )

        blocked = tuple(
            BlockedCategory(
                category=str(item.get("category") or ""),
                reason=str(item.get("reason") or ""),
            )
            for item in (raw.get("blocked") or [])
            if isinstance(item, dict) and item.get("category")
        )

        return cls(
            actions=actions,
            blocked=blocked,
            contract_version=str(raw.get("contractVersion") or "unknown"),
        )

    # -- queries ----------------------------------------------------------

    @property
    def action_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._actions))

    @property
    def actions(self) -> tuple[ActionDefinition, ...]:
        return tuple(self._actions[key] for key in sorted(self._actions))

    @property
    def blocked_categories(self) -> tuple[BlockedCategory, ...]:
        return self._blocked

    def get(self, action_type: str) -> ActionDefinition | None:
        return self._actions.get(action_type)

    def is_registered(self, action_type: str) -> bool:
        return action_type in self._actions

    def blocked_match(self, action_type: str) -> BlockedCategory | None:
        """Return the blocked category an action type falls under, if any.

        Matching is exact or dotted-prefix: ``shell.exec`` matches the
        ``shell.exec`` category, and a hypothetical ``shell.exec.powershell``
        matches it too. Substring matching is deliberately avoided - it would
        make ``app.open`` collide with an ``open`` category and produce
        confusing rejections.
        """
        if not action_type:
            return None
        for entry in self._blocked:
            if action_type == entry.category or action_type.startswith(entry.category + "."):
                return entry
        return None


def risk_at_least(tier: str, threshold: str) -> bool:
    """True when ``tier`` is at or above ``threshold`` in severity."""
    return RISK_ORDER.get(tier, -1) >= RISK_ORDER.get(threshold, len(RISK_TIERS))


def highest_risk_tier(tiers: list[str]) -> str:
    """Most severe tier in ``tiers``; ``T0`` for an empty list."""
    known = [tier for tier in tiers if tier in RISK_ORDER]
    if not known:
        return "T0"
    return max(known, key=lambda tier: RISK_ORDER[tier])
