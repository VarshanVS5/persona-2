"""Deterministic fixture planner (mock/offline path).

OWNER: Person 2 - Voice, Intent & 9Router.

Acceptance checklist item: "Mock mode and text fallback work."

The fixture planner is what Person 4's end-to-end mock workflow and the demo
run against, so its output must be byte-compatible with the canonical
``packages/contracts/examples/plan.valid.json`` shape and must pass the same
validator as live output.
"""

from __future__ import annotations

import pytest

from atlas.planner.base import PlanOutcomeKind
from atlas.planner.digest import compute_plan_digest
from atlas.planner.fixture_planner import FixturePlanner

TASK_ID = "task-fix-1"


@pytest.fixture
def planner(registry):
    return FixturePlanner(registry)


# -- matching -------------------------------------------------------------


@pytest.mark.parametrize(
    "request_text",
    [
        "open my editor",
        "Open Notepad",
        "please launch notepad for me",
        "start the text editor",
        "bring up my notes",
    ],
)
def test_supported_requests_produce_a_plan(planner, request_text):
    outcome = planner.plan(TASK_ID, request_text)

    assert outcome.kind is PlanOutcomeKind.PLAN, outcome.describe()
    assert outcome.plan["steps"][0]["actionType"] == "app.open"


def test_app_keyword_without_a_verb_asks_rather_than_guesses(planner):
    """"notepad" alone is not a command (Master PRD FR-04)."""
    outcome = planner.plan(TASK_ID, "notepad")

    assert outcome.kind is PlanOutcomeKind.CLARIFICATION


def test_verb_without_a_known_target_asks_for_clarification(planner):
    outcome = planner.plan(TASK_ID, "open the thing")

    assert outcome.kind is PlanOutcomeKind.CLARIFICATION
    assert outcome.clarification_question


def test_unsupported_request_lists_what_is_actually_supported(planner, registry):
    outcome = planner.plan(TASK_ID, "send an email to my manager")

    assert outcome.kind is PlanOutcomeKind.CLARIFICATION
    joined = " ".join(outcome.clarification_options)
    for action in registry.actions:
        assert action.action_type in joined


def test_blocked_request_never_produces_a_plan(planner):
    for request in (
        "delete all my files",
        "run powershell for me",
        "type my password",
        "buy this for me",
    ):
        assert planner.plan(TASK_ID, request).kind is PlanOutcomeKind.CLARIFICATION, request


# -- plan content ---------------------------------------------------------


def test_plan_matches_the_canonical_example_shape(planner, valid_plan):
    outcome = planner.plan(TASK_ID, "open my editor")
    produced = outcome.plan

    assert set(produced) == set(valid_plan)
    assert set(produced["steps"][0]) == set(valid_plan["steps"][0])


def test_plan_fields_come_from_the_registry(planner, registry):
    definition = registry.get("app.open")
    step = planner.plan(TASK_ID, "open my editor").plan["steps"][0]

    assert step["riskTier"] == definition.risk_tier
    assert step["timeoutMs"] == definition.timeout_ms
    assert {c["type"] for c in step["preconditions"]} == set(definition.precondition_types)
    assert {c["type"] for c in step["postconditions"]} == set(definition.postcondition_types)


def test_plan_carries_the_task_id_and_request_verbatim(planner):
    outcome = planner.plan(TASK_ID, "open my editor")

    assert outcome.plan["taskId"] == TASK_ID
    assert outcome.plan["userRequest"] == "open my editor"
    assert outcome.plan["steps"][0]["idempotencyKey"] == f"{TASK_ID}-step-1"


def test_fixture_plan_always_requires_confirmation(planner):
    outcome = planner.plan(TASK_ID, "open my editor")

    assert outcome.plan["requiresConfirmation"] is True
    assert outcome.requires_confirmation is True


# -- it goes through the same gate ---------------------------------------


def test_fixture_output_passes_the_shared_validator(planner, validator):
    outcome = planner.plan(TASK_ID, "open my editor")
    result = validator.validate(
        outcome.plan, expected_task_id=TASK_ID, expected_user_request="open my editor"
    )

    assert result.valid, result.describe()


def test_a_broken_fixture_planner_is_rejected_not_trusted(registry, monkeypatch):
    """Local origin does not grant trust: fixture plans are validated too."""
    planner = FixturePlanner(registry)

    def broken(task_id, user_request, app_id):
        return {
            "taskId": task_id,
            "userRequest": user_request,
            "summary": "broken",
            "requiresConfirmation": False,
            "steps": [
                {
                    "stepId": 1,
                    "actionType": "shell.exec",
                    "arguments": {},
                    "riskTier": "T0",
                    "preconditions": [],
                    "postconditions": [],
                    "timeoutMs": 10000,
                    "idempotencyKey": f"{task_id}-step-1",
                }
            ],
        }

    monkeypatch.setattr(planner, "_build_app_open_plan", broken)
    outcome = planner.plan(TASK_ID, "open my editor")

    assert outcome.kind is PlanOutcomeKind.REJECTED
    assert outcome.plan is None
    assert any("defect" in r.message for r in outcome.rejections)


# -- determinism and labelling -------------------------------------------


def test_identical_requests_produce_an_identical_digest(planner):
    first = planner.plan(TASK_ID, "open my editor")
    second = planner.plan(TASK_ID, "open my editor")

    assert first.plan_digest == second.plan_digest
    assert first.plan_digest == compute_plan_digest(first.plan)


def test_every_fixture_outcome_is_labelled_mocked(planner):
    assert planner.plan(TASK_ID, "open my editor").mocked is True
    assert planner.plan(TASK_ID, "unsupported request").mocked is True
    assert planner.is_mock is True


def test_fixture_planner_spends_no_provider_attempts(planner):
    assert planner.plan(TASK_ID, "open my editor").attempts == 0


def test_planner_satisfies_the_shared_protocol(planner):
    from atlas.planner.base import Planner

    assert isinstance(planner, Planner)


# -- registry-driven degradation -----------------------------------------


def test_planner_offers_nothing_when_the_action_is_not_registered(registry):
    """If ``app.open`` left the registry, improvising would be wrong."""

    class EmptyRegistry:
        contract_version = "1.0.0"
        actions = ()
        action_types = ()
        blocked_categories = ()

        def is_registered(self, action_type):
            return False

        def get(self, action_type):
            return None

        def blocked_match(self, action_type):
            return None

    planner = FixturePlanner(EmptyRegistry())
    outcome = planner.plan(TASK_ID, "open my editor")

    assert outcome.kind is PlanOutcomeKind.CLARIFICATION
