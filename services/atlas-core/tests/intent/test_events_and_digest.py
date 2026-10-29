"""Event envelopes and the plan digest.

OWNER: Person 2 - Voice, Intent & 9Router.

Covers the events this workstream provides to Person 3 (``transcript``,
``plan_ready``, ``clarification_required``, ``provider_error``) and the digest
rule from ``docs/api-contract.md`` that binds a confirmation to the exact plan
the user reviewed.

Every event is validated against ``packages/contracts/events.schema.json``
where the installed validator supports it; the test reports a skip rather than
passing silently when it does not.
"""

from __future__ import annotations

import copy
import json

import pytest

from atlas.planner.base import PlanOutcome, PlanOutcomeKind
from atlas.planner.contracts import validate_event, validator_info
from atlas.planner.digest import canonical_json, compute_plan_digest, verify_digest
from atlas.planner.events import (
    SCHEMA_VERSION,
    clarification_required_event,
    confirmation_required_event,
    events_for_outcome,
    plan_ready_event,
    provider_error_event,
    transcript_event,
    utc_timestamp,
)
from atlas.planner.validation import Rejection, RejectionCode
from atlas.providers.base import ProviderError, ProviderErrorCode
from atlas.providers.redaction import register_secret

TASK_ID = "task-evt-1"


def _assert_schema_valid(event: dict) -> None:
    info = validator_info()
    if not info.events_supported:
        pytest.skip(f"event schema validation unavailable: {info.describe()}")
    violations = validate_event(event)
    assert not violations, "; ".join(str(v) for v in violations)


# -- envelope -------------------------------------------------------------


def test_transcript_event_is_schema_valid():
    event = transcript_event(TASK_ID, "open my editor", is_final=True, source="text")

    assert event["schemaVersion"] == SCHEMA_VERSION
    assert event["taskId"] == TASK_ID
    assert event["type"] == "transcript"
    assert event["payload"] == {"text": "open my editor", "isFinal": True, "source": "text"}
    _assert_schema_valid(event)


def test_timestamp_uses_rfc3339_zulu():
    """``+00:00`` would not match the example files' ``Z`` form."""
    stamp = utc_timestamp()

    assert stamp.endswith("Z")
    assert "+00:00" not in stamp
    _assert_schema_valid(transcript_event(TASK_ID, "x", is_final=True, source="text"))


def test_empty_task_id_is_refused():
    with pytest.raises(ValueError):
        transcript_event("", "x", is_final=True, source="text")


def test_invalid_transcript_source_is_refused():
    with pytest.raises(ValueError):
        transcript_event(TASK_ID, "x", is_final=True, source="keyboard")


def test_plan_ready_event_matches_the_contract_example(valid_plan):
    digest = compute_plan_digest(valid_plan)
    event = plan_ready_event(valid_plan["taskId"], valid_plan, digest)

    assert event["type"] == "plan_ready"
    assert event["payload"]["planDigest"] == digest
    assert event["payload"]["plan"] == valid_plan
    _assert_schema_valid(event)


def test_plan_ready_without_a_digest_is_refused(valid_plan):
    """Without a digest, confirmation cannot be bound to this plan."""
    with pytest.raises(ValueError):
        plan_ready_event(TASK_ID, valid_plan, "")


def test_confirmation_required_event_is_schema_valid():
    event = confirmation_required_event(TASK_ID, "sha256:abc", "T1")

    assert event["payload"] == {"planDigest": "sha256:abc", "highestRiskTier": "T1"}
    _assert_schema_valid(event)


def test_confirmation_required_rejects_an_unknown_tier():
    with pytest.raises(ValueError):
        confirmation_required_event(TASK_ID, "sha256:abc", "T9")


def test_clarification_event_is_schema_valid():
    event = clarification_required_event(TASK_ID, "Which editor?", ["Notepad", "VS Code"])

    assert event["payload"]["question"] == "Which editor?"
    assert event["payload"]["options"] == ["Notepad", "VS Code"]
    _assert_schema_valid(event)


def test_clarification_requires_a_question():
    with pytest.raises(ValueError):
        clarification_required_event(TASK_ID, "   ")


def test_provider_error_event_is_schema_valid():
    error = ProviderError(
        provider="9router", code=ProviderErrorCode.TIMEOUT, message="no response in 20s"
    )
    event = provider_error_event(TASK_ID, error)

    assert event["payload"]["provider"] == "9router"
    assert event["payload"]["code"] == "TIMEOUT"
    assert event["payload"]["retryable"] is True
    _assert_schema_valid(event)


def test_provider_name_outside_the_enum_is_refused():
    """An invented provider name would emit a schema-invalid event."""
    with pytest.raises(ValueError):
        ProviderError(provider="openai", code=ProviderErrorCode.TIMEOUT)


def test_event_payloads_are_redacted(valid_plan):
    register_secret("event-leak-secret-abc123")
    plan = copy.deepcopy(valid_plan)
    plan["summary"] = "open with key event-leak-secret-abc123"

    event = plan_ready_event(TASK_ID, plan, "sha256:abc")
    assert "event-leak-secret-abc123" not in json.dumps(event)


# -- outcome mapping ------------------------------------------------------


def test_plan_outcome_emits_plan_ready_and_confirmation(valid_plan):
    outcome = PlanOutcome(
        kind=PlanOutcomeKind.PLAN,
        plan=valid_plan,
        plan_digest=compute_plan_digest(valid_plan),
        highest_risk_tier="T1",
        requires_confirmation=True,
    )
    events = events_for_outcome(TASK_ID, outcome)

    assert [e["type"] for e in events] == ["plan_ready", "confirmation_required"]
    for event in events:
        _assert_schema_valid(event)


def test_plan_outcome_without_confirmation_emits_only_plan_ready(valid_plan):
    outcome = PlanOutcome(
        kind=PlanOutcomeKind.PLAN,
        plan=valid_plan,
        plan_digest="sha256:abc",
        highest_risk_tier="T0",
        requires_confirmation=False,
    )
    assert [e["type"] for e in events_for_outcome(TASK_ID, outcome)] == ["plan_ready"]


def test_clarification_outcome_emits_clarification():
    outcome = PlanOutcome.clarification("Which editor?", options=("Notepad",))
    events = events_for_outcome(TASK_ID, outcome)

    assert [e["type"] for e in events] == ["clarification_required"]


def test_validation_rejection_is_not_reported_as_a_provider_error():
    """Calling a local policy rejection a "provider error" would misdirect
    both the user and the defect report."""
    outcome = PlanOutcome.rejected(
        rejections=(Rejection(code=RejectionCode.BLOCKED_ACTION, message="nope"),)
    )
    assert events_for_outcome(TASK_ID, outcome) == []


def test_provider_failure_outcome_emits_provider_error():
    outcome = PlanOutcome.rejected(
        provider_error=ProviderError(provider="9router", code=ProviderErrorCode.UPSTREAM)
    )
    assert [e["type"] for e in events_for_outcome(TASK_ID, outcome)] == ["provider_error"]


# -- digest ---------------------------------------------------------------


def test_digest_is_prefixed_and_stable(valid_plan):
    digest = compute_plan_digest(valid_plan)

    assert digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 64
    assert digest == compute_plan_digest(copy.deepcopy(valid_plan))


def test_digest_ignores_key_order(valid_plan):
    reordered = {key: valid_plan[key] for key in reversed(list(valid_plan))}
    assert compute_plan_digest(reordered) == compute_plan_digest(valid_plan)


def test_canonical_json_is_compact_and_sorted():
    text = canonical_json({"b": 1, "a": 2})
    assert text == '{"a":2,"b":1}'


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.__setitem__("requiresConfirmation", False),
        lambda p: p["steps"][0].__setitem__("arguments", {"appId": "calc"}),
        lambda p: p["steps"][0].__setitem__("riskTier", "T2"),
        lambda p: p["steps"][0].__setitem__("timeoutMs", 5000),
        lambda p: p.__setitem__("summary", "something else"),
        lambda p: p["steps"].append(copy.deepcopy(p["steps"][0])),
    ],
)
def test_any_meaningful_change_changes_the_digest(valid_plan, mutate):
    """This is what makes "the plan was never silently replaced" checkable."""
    original = compute_plan_digest(valid_plan)
    mutated = copy.deepcopy(valid_plan)
    mutate(mutated)

    assert compute_plan_digest(mutated) != original


def test_verify_digest_accepts_the_reviewed_plan(valid_plan):
    assert verify_digest(valid_plan, compute_plan_digest(valid_plan))


def test_verify_digest_tolerates_surrounding_whitespace(valid_plan):
    digest = compute_plan_digest(valid_plan)
    assert verify_digest(valid_plan, f"  {digest}\n")


def test_verify_digest_rejects_a_swapped_plan(valid_plan):
    digest = compute_plan_digest(valid_plan)
    swapped = copy.deepcopy(valid_plan)
    swapped["steps"][0]["arguments"]["appId"] = "calc"

    assert not verify_digest(swapped, digest)


@pytest.mark.parametrize("bad", ["", "   ", "sha256:", "not-a-digest"])
def test_verify_digest_rejects_malformed_input(valid_plan, bad):
    assert not verify_digest(valid_plan, bad)
