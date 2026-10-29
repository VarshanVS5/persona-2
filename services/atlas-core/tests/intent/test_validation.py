"""Plan validation against the shared contracts.

OWNER: Person 2 - Voice, Intent & 9Router.

Acceptance checklist items covered:
- "Structured output is schema-validated."
- "Unknown actions and invalid JSON are rejected."

The three canonical example files in ``packages/contracts/examples/`` are the
primary assertions: ``plan.valid.json`` must pass, and both ``plan.invalid-*``
files must be rejected for the reason their filename claims
(``INSTRUCTIONS.md`` section 6: "reproduce the rejections in
``examples/plan.invalid-*.json``").
"""

from __future__ import annotations

import copy
import json

import pytest
from conftest import EXAMPLES_DIR

from atlas.planner.registry import CONFIRMATION_REQUIRED_FROM_TIER
from atlas.planner.validation import MAX_STEPS, RejectionCode


def _load_example(name: str) -> dict:
    with (EXAMPLES_DIR / name).open("r", encoding="utf-8") as handle:
        return json.load(handle)


# -- canonical contract examples -----------------------------------------


def test_canonical_valid_example_passes(validator):
    plan = _load_example("plan.valid.json")
    result = validator.validate(plan)

    assert result.valid, result.describe()
    assert result.highest_risk_tier == "T1"
    assert result.requires_confirmation is True


def test_canonical_unknown_action_example_is_rejected(validator):
    """``shell.exec`` is both unregistered and in the blocked list.

    The blocked reason is asserted because that is the message the user needs:
    "ATLAS cannot run shell commands", not "unknown action type".
    """
    plan = _load_example("plan.invalid-unknown-action.json")
    result = validator.validate(plan)

    assert not result.valid
    assert result.has(RejectionCode.BLOCKED_ACTION)
    reason = next(r for r in result.rejections if r.code == RejectionCode.BLOCKED_ACTION)
    assert "shell.exec" in reason.message
    assert reason.step_id == 1


def test_canonical_lowered_risk_example_is_rejected(validator):
    """The model declared T0 for an action the registry fixes at T1."""
    plan = _load_example("plan.invalid-lowered-risk.json")
    result = validator.validate(plan)

    assert not result.valid
    assert result.has(RejectionCode.RISK_TIER_MISMATCH)
    assert result.has(RejectionCode.CONFIRMATION_LOWERED)


# -- schema layer ---------------------------------------------------------


@pytest.mark.parametrize("bad", [None, [], "a plan", 42, True])
def test_non_object_is_rejected(validator, bad):
    result = validator.validate(bad)
    assert not result.valid
    assert result.has(RejectionCode.NOT_AN_OBJECT)


def test_missing_required_field_is_rejected(validator, valid_plan):
    del valid_plan["requiresConfirmation"]
    result = validator.validate(valid_plan)

    assert not result.valid
    assert result.has(RejectionCode.SCHEMA_INVALID)


def test_additional_property_is_rejected(validator, valid_plan):
    """``additionalProperties: false`` stops an unknown field smuggling data."""
    valid_plan["executeImmediately"] = True
    result = validator.validate(valid_plan)

    assert not result.valid
    assert result.has(RejectionCode.SCHEMA_INVALID)


def test_additional_property_on_step_is_rejected(validator, valid_plan):
    valid_plan["steps"][0]["rawCommand"] = "del /s C:\\"
    result = validator.validate(valid_plan)

    assert not result.valid
    assert result.has(RejectionCode.SCHEMA_INVALID)


def test_invalid_risk_tier_value_is_rejected(validator, valid_plan):
    valid_plan["steps"][0]["riskTier"] = "T9"
    result = validator.validate(valid_plan)

    assert not result.valid
    assert result.has(RejectionCode.SCHEMA_INVALID)


def test_schema_violation_is_attributed_to_its_step(validator, valid_plan):
    valid_plan["steps"][0]["timeoutMs"] = 0  # schema minimum is 1
    result = validator.validate(valid_plan)

    assert not result.valid
    violation = next(r for r in result.rejections if r.code == RejectionCode.SCHEMA_INVALID)
    assert violation.step_id == 1


# -- identity -------------------------------------------------------------


def test_task_id_mismatch_is_rejected(validator, valid_plan):
    """The model does not get to choose the task identity."""
    result = validator.validate(valid_plan, expected_task_id="task-9999")

    assert not result.valid
    assert result.has(RejectionCode.TASK_ID_MISMATCH)


def test_user_request_mismatch_is_rejected(validator, valid_plan):
    result = validator.validate(valid_plan, expected_user_request="something else entirely")

    assert not result.valid
    assert result.has(RejectionCode.USER_REQUEST_MISMATCH)


def test_matching_identity_passes(validator, valid_plan):
    result = validator.validate(
        valid_plan,
        expected_task_id=valid_plan["taskId"],
        expected_user_request=valid_plan["userRequest"],
    )
    assert result.valid, result.describe()


# -- registry conformance -------------------------------------------------


def test_unregistered_non_blocked_action_is_rejected(validator, valid_plan):
    valid_plan["steps"][0]["actionType"] = "window.resize"
    result = validator.validate(valid_plan)

    assert not result.valid
    assert result.has(RejectionCode.UNKNOWN_ACTION)
    message = next(r for r in result.rejections if r.code == RejectionCode.UNKNOWN_ACTION).message
    assert "app.open" in message  # tells the caller what *is* registered


@pytest.mark.parametrize(
    "action_type",
    [
        "credential.entry",
        "shell.exec",
        "security.uac_bypass",
        "file.delete_unrestricted",
        "message.send",
        "payment.purchase",
        "security.settings_change",
    ],
)
def test_every_blocked_category_is_rejected(validator, valid_plan, action_type):
    valid_plan["steps"][0]["actionType"] = action_type
    result = validator.validate(valid_plan)

    assert not result.valid
    assert result.has(RejectionCode.BLOCKED_ACTION)


def test_blocked_match_uses_dotted_prefix_not_substring(registry):
    """``shell.exec.powershell`` is blocked; ``app.open`` is not caught by it."""
    assert registry.blocked_match("shell.exec.powershell") is not None
    assert registry.blocked_match("app.open") is None
    assert registry.blocked_match("myshell.exec") is None


def test_missing_required_argument_is_rejected(validator, valid_plan):
    valid_plan["steps"][0]["arguments"] = {}
    result = validator.validate(valid_plan)

    assert not result.valid
    assert result.has(RejectionCode.INVALID_ARGUMENTS)


def test_unexpected_argument_is_rejected(validator, valid_plan):
    """``argumentsSchema`` sets ``additionalProperties: false``."""
    valid_plan["steps"][0]["arguments"] = {"appId": "notepad", "args": "/c calc.exe"}
    result = validator.validate(valid_plan)

    assert not result.valid
    assert result.has(RejectionCode.INVALID_ARGUMENTS)


def test_wrong_argument_type_is_rejected(validator, valid_plan):
    valid_plan["steps"][0]["arguments"] = {"appId": ["notepad"]}
    result = validator.validate(valid_plan)

    assert not result.valid
    assert result.has(RejectionCode.INVALID_ARGUMENTS)


def test_raised_timeout_is_rejected(validator, valid_plan, registry):
    ceiling = registry.get("app.open").timeout_ms
    valid_plan["steps"][0]["timeoutMs"] = ceiling + 1
    result = validator.validate(valid_plan)

    assert not result.valid
    assert result.has(RejectionCode.TIMEOUT_OUT_OF_BOUNDS)


def test_lowered_timeout_is_allowed(validator, valid_plan, registry):
    """A shorter timeout is more conservative, so it is permitted."""
    valid_plan["steps"][0]["timeoutMs"] = registry.get("app.open").timeout_ms - 1
    result = validator.validate(valid_plan)

    assert result.valid, result.describe()


def test_dropped_postcondition_is_rejected(validator, valid_plan):
    """Without a postcondition the result cannot be verified."""
    valid_plan["steps"][0]["postconditions"] = []
    result = validator.validate(valid_plan)

    assert not result.valid
    assert result.has(RejectionCode.MISSING_POSTCONDITION)


def test_dropped_precondition_is_rejected(validator, valid_plan):
    valid_plan["steps"][0]["preconditions"] = []
    result = validator.validate(valid_plan)

    assert not result.valid
    assert result.has(RejectionCode.MISSING_PRECONDITION)


def test_condition_args_are_not_compared_to_registry_templates(validator, valid_plan):
    """The registry stores templates; plans carry materialised args.

    Guards the decision documented in ``validation._validate_step``: comparing
    args would reject the canonical valid example.
    """
    valid_plan["steps"][0]["preconditions"] = [
        {"type": "app.allowlisted", "args": {"appId": "notepad", "resolvedBy": "registry"}}
    ]
    result = validator.validate(valid_plan)
    assert result.valid, result.describe()


# -- plan-level invariants ------------------------------------------------


def test_empty_steps_is_rejected(validator, valid_plan):
    valid_plan["steps"] = []
    result = validator.validate(valid_plan)

    assert not result.valid
    assert result.has(RejectionCode.EMPTY_PLAN)


def test_too_many_steps_is_rejected(validator, valid_plan):
    template = valid_plan["steps"][0]
    valid_plan["steps"] = []
    for index in range(MAX_STEPS + 1):
        step = copy.deepcopy(template)
        step["stepId"] = index + 1
        step["idempotencyKey"] = f"task-0001-step-{index + 1}"
        valid_plan["steps"].append(step)

    result = validator.validate(valid_plan)
    assert not result.valid
    assert result.has(RejectionCode.PLAN_TOO_LARGE)


def test_non_contiguous_step_ids_are_rejected(validator, valid_plan):
    valid_plan["steps"][0]["stepId"] = 7
    result = validator.validate(valid_plan)

    assert not result.valid
    assert result.has(RejectionCode.STEP_SEQUENCE_INVALID)


def test_duplicate_idempotency_key_is_rejected(validator, valid_plan):
    """Duplicate keys make retry-safety undecidable."""
    second = copy.deepcopy(valid_plan["steps"][0])
    second["stepId"] = 2
    valid_plan["steps"].append(second)

    result = validator.validate(valid_plan)
    assert not result.valid
    assert result.has(RejectionCode.DUPLICATE_IDEMPOTENCY_KEY)


def test_two_distinct_steps_are_allowed(validator, valid_plan):
    second = copy.deepcopy(valid_plan["steps"][0])
    second["stepId"] = 2
    second["idempotencyKey"] = "task-0001-step-2"
    valid_plan["steps"].append(second)

    result = validator.validate(valid_plan)
    assert result.valid, result.describe()


def test_confirmation_cannot_be_waived_at_or_above_threshold(validator, valid_plan):
    assert CONFIRMATION_REQUIRED_FROM_TIER == "T1"
    valid_plan["requiresConfirmation"] = False
    result = validator.validate(valid_plan)

    assert not result.valid
    assert result.has(RejectionCode.CONFIRMATION_LOWERED)


def test_confirmation_is_never_lowered_on_a_valid_plan(validator, valid_plan):
    """Local code decides consent; it may raise the requirement, never lower it."""
    result = validator.validate(valid_plan)
    assert result.requires_confirmation is True


def test_rejected_result_always_requires_confirmation(validator, valid_plan):
    """A rejected plan must never read as 'consent waived'."""
    valid_plan["steps"][0]["actionType"] = "shell.exec"
    result = validator.validate(valid_plan)

    assert not result.valid
    assert result.requires_confirmation is True


def test_all_problems_are_reported_together(validator, valid_plan):
    """Collecting every rejection is what makes repair prompts useful."""
    valid_plan["steps"][0]["riskTier"] = "T0"
    valid_plan["steps"][0]["timeoutMs"] = 999_999
    valid_plan["requiresConfirmation"] = False

    result = validator.validate(valid_plan)
    codes = set(result.codes)
    assert {
        RejectionCode.RISK_TIER_MISMATCH,
        RejectionCode.TIMEOUT_OUT_OF_BOUNDS,
        RejectionCode.CONFIRMATION_LOWERED,
    } <= codes


def test_result_serialization_is_json_safe(validator, valid_plan):
    valid_plan["steps"][0]["actionType"] = "shell.exec"
    result = validator.validate(valid_plan)

    encoded = json.dumps(result.to_dict())
    assert "BLOCKED_ACTION" in encoded
