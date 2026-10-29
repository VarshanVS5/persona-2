"""OWNER: Person 2 - Voice, Intent & 9Router. Do not edit without owner approval.

Structured task-plan generation and strict validation against
``packages/contracts/task-plan.schema.json``.

Entry point for the orchestrator: :class:`PlanningService`.

    from atlas.planner import PlanningService

    service = PlanningService.from_env()
    result = service.plan_text(task_id, user_request)

A plan returned from here has passed schema and action-registry conformance.
It is **not** authorized to execute: allowlist resolution, consent state, and
execution remain with Person 1's policy engine and runtime.
"""

from .base import PlanOutcome, PlanOutcomeKind, Planner
from .contracts import (
    ContractsUnavailable,
    contract_version,
    contracts_dir,
    load_action_registry,
    load_events_schema,
    load_task_plan_schema,
    validate_event,
    validate_task_plan_schema,
    validator_info,
)
from .digest import canonical_json, compute_plan_digest, verify_digest
from .events import (
    SCHEMA_VERSION,
    clarification_required_event,
    confirmation_required_event,
    events_for_outcome,
    plan_ready_event,
    provider_error_event,
    transcript_event,
)
from .fixture_planner import FixturePlanner, build_fixture_planner
from .llm_planner import LlmPlanner
from .prompt import build_system_prompt, build_user_prompt
from .registry import (
    BLOCKED_RISK_TIERS,
    CONFIRMATION_REQUIRED_FROM_TIER,
    RISK_TIERS,
    ActionDefinition,
    ActionRegistry,
    highest_risk_tier,
    risk_at_least,
)
from .service import PlanningResult, PlanningService
from .validation import (
    MAX_STEPS,
    PlanValidator,
    Rejection,
    RejectionCode,
    ValidationResult,
)

__all__ = [
    "BLOCKED_RISK_TIERS",
    "CONFIRMATION_REQUIRED_FROM_TIER",
    "MAX_STEPS",
    "RISK_TIERS",
    "SCHEMA_VERSION",
    "ActionDefinition",
    "ActionRegistry",
    "ContractsUnavailable",
    "FixturePlanner",
    "LlmPlanner",
    "PlanOutcome",
    "PlanOutcomeKind",
    "PlanValidator",
    "Planner",
    "PlanningResult",
    "PlanningService",
    "Rejection",
    "RejectionCode",
    "ValidationResult",
    "build_fixture_planner",
    "build_system_prompt",
    "build_user_prompt",
    "canonical_json",
    "clarification_required_event",
    "compute_plan_digest",
    "confirmation_required_event",
    "contract_version",
    "contracts_dir",
    "events_for_outcome",
    "highest_risk_tier",
    "load_action_registry",
    "load_events_schema",
    "load_task_plan_schema",
    "plan_ready_event",
    "provider_error_event",
    "risk_at_least",
    "transcript_event",
    "validate_event",
    "validate_task_plan_schema",
    "validator_info",
    "verify_digest",
]
