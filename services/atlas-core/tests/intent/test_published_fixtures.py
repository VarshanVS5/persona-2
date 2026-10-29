"""The fixtures published for Person 4 must keep doing what they claim.

OWNER: Person 2 - Voice, Intent & 9Router.

``INSTRUCTIONS.md`` section 5 - provides to Person 4: "valid/invalid plan
fixtures, provider mocks, provider-failure tests".

A fixture folder with a README is a promise. This file turns that promise into
a test: every entry in ``fixtures/expectations.json`` is replayed through the
real validator and the real provider-response parser. If a contract change or a
code change stops a fixture reproducing its declared rejection, this fails here
rather than silently misleading Person 4's integration suite.

Fixture location note: ``OWNERSHIP.json`` records Person 2's working folder as
``workstreams/person-2-voice-9router/``, but in this workspace it is
``reference/person-2-voice-9router/``. Both are probed, so these tests keep
working whichever location the repository settles on (see the change request
``20261029-project-owner-workstream-folder-path.md``).
"""

from __future__ import annotations

import json

import pytest
from conftest import REPO_ROOT, SHORT_CREDENTIAL, make_settings

from atlas.planner.llm_planner import LlmPlanner
from atlas.planner.base import PlanOutcomeKind
from atlas.planner.validation import RejectionCode
from atlas.providers.base import ProviderErrorCode
from atlas.providers.nine_router import MockNineRouterClient, NineRouterClient

_CANDIDATE_DIRS = (
    REPO_ROOT / "reference" / "person-2-voice-9router" / "fixtures",
    REPO_ROOT / "workstreams" / "person-2-voice-9router" / "fixtures",
)


def _fixtures_dir():
    for candidate in _CANDIDATE_DIRS:
        if (candidate / "expectations.json").exists():
            return candidate
    pytest.skip("published fixtures folder not found")


FIXTURES = _fixtures_dir()


def _load(relative: str) -> dict:
    with (FIXTURES / relative).open("r", encoding="utf-8") as handle:
        return json.load(handle)


EXPECTATIONS = _load("expectations.json")
PLAN_CASES = EXPECTATIONS["plans"]
PROVIDER_CASES = EXPECTATIONS["providerFixtures"]


# -- plan fixtures --------------------------------------------------------


def test_expectations_cover_every_published_plan_file():
    """No orphan fixture: a file Person 4 can see must have a declared outcome."""
    on_disk = {f"plans/{path.name}" for path in (FIXTURES / "plans").glob("*.json")}
    declared = {case["file"] for case in PLAN_CASES}

    assert on_disk == declared


@pytest.mark.parametrize("case", PLAN_CASES, ids=[c["file"] for c in PLAN_CASES])
def test_plan_fixture_reproduces_its_declared_outcome(validator, case):
    plan = _load(case["file"])
    result = validator.validate(plan)

    assert result.valid is case["valid"], result.describe()

    expected = {RejectionCode(code) for code in case["expectedCodes"]}
    actual = set(result.codes)
    missing = expected - actual
    assert not missing, f"{case['file']} no longer produces {sorted(c.value for c in missing)}"


@pytest.mark.parametrize(
    "case", [c for c in PLAN_CASES if c["valid"]], ids=lambda c: c["file"]
)
def test_valid_fixture_matches_its_declared_tier_and_consent(validator, case):
    result = validator.validate(_load(case["file"]))

    assert result.highest_risk_tier == case["highestRiskTier"]
    assert result.requires_confirmation is case["requiresConfirmation"]


@pytest.mark.parametrize(
    "case", [c for c in PLAN_CASES if not c["valid"]], ids=lambda c: c["file"]
)
def test_invalid_fixture_never_yields_a_usable_plan(validator, case):
    result = validator.validate(_load(case["file"]))

    assert not result.valid
    assert result.plan is None
    assert result.requires_confirmation is True


def test_every_invalid_fixture_explains_itself():
    """Person 4 needs to know *why* a fixture is expected to fail."""
    for case in PLAN_CASES:
        assert case.get("note", "").strip(), case["file"]


def test_valid_fixture_is_what_the_fixture_planner_produces(registry):
    """The published demo plan must not drift from the planner's real output."""
    from atlas.planner.fixture_planner import FixturePlanner

    published = _load("plans/plan.valid.app-open.json")
    produced = FixturePlanner(registry).plan(published["taskId"], published["userRequest"]).plan

    assert produced == published


# -- provider fixtures ----------------------------------------------------


def test_expectations_cover_every_published_provider_file():
    on_disk = {f"provider/{path.name}" for path in (FIXTURES / "provider").glob("*.json")}
    declared = {case["file"] for case in PROVIDER_CASES}

    assert on_disk == declared


def _parse_provider_body(body: dict):
    """Run a fixture body through the real client parser, not a reimplementation."""
    settings = make_settings(
        SHORT_CREDENTIAL,
        mock_mode=False,
        nine_router_base_url="http://127.0.0.1:1",
        nine_router_model="m",
    )
    client = NineRouterClient(settings)
    return client._parse_success_body(json.dumps(body).encode("utf-8"), 1)


@pytest.mark.parametrize(
    "case",
    [c for c in PROVIDER_CASES if c["kind"] == "success"],
    ids=lambda c: c["file"],
)
def test_success_provider_fixtures_parse_to_content(case):
    result = _parse_provider_body(_load(case["file"]))

    assert result.ok, case["file"]
    assert result.unwrap().strip()


@pytest.mark.parametrize(
    "case",
    [c for c in PROVIDER_CASES if c["kind"] == "malformed"],
    ids=lambda c: c["file"],
)
def test_malformed_provider_fixtures_never_parse_to_success(case):
    result = _parse_provider_body(_load(case["file"]))

    assert not result.ok, case["file"]
    assert result.error.code in (
        ProviderErrorCode.MALFORMED_RESPONSE,
        ProviderErrorCode.UPSTREAM,
    )


# -- provider fixture -> planner outcome ---------------------------------


def _plan_from_fixture(registry, fixture_file: str, task_id: str, request: str):
    content = _parse_provider_body(_load(fixture_file)).unwrap()
    planner = LlmPlanner(MockNineRouterClient(responses=[content]), registry)
    return planner.plan(task_id, request)


def test_valid_plan_fixture_drives_the_planner_to_a_plan(registry):
    outcome = _plan_from_fixture(
        registry, "provider/9router.response.valid-plan.json", "task-f001", "open my editor"
    )

    assert outcome.kind is PlanOutcomeKind.PLAN
    assert outcome.plan_digest.startswith("sha256:")


def test_prose_wrapped_fixture_still_yields_a_plan(registry):
    outcome = _plan_from_fixture(
        registry,
        "provider/9router.response.prose-wrapped-plan.json",
        "task-f001",
        "open my editor",
    )
    assert outcome.kind is PlanOutcomeKind.PLAN


def test_clarification_fixture_yields_a_question(registry):
    outcome = _plan_from_fixture(
        registry, "provider/9router.response.clarification.json", "task-f001", "open it"
    )

    assert outcome.kind is PlanOutcomeKind.CLARIFICATION
    assert outcome.clarification_options == ("Notepad", "WordPad")


def test_blocked_action_fixture_is_refused_by_the_gate(registry):
    """A well-formed model reply reaching for a blocked action still fails."""
    outcome = _plan_from_fixture(
        registry,
        "provider/9router.response.blocked-action-plan.json",
        "task-f002",
        "run this command for me",
    )

    assert outcome.kind is PlanOutcomeKind.REJECTED
    assert any(r.code is RejectionCode.BLOCKED_ACTION for r in outcome.rejections)
    assert outcome.plan is None


# -- hygiene --------------------------------------------------------------


def test_no_fixture_contains_a_credential_shape():
    """Fixtures are committed, so they must not carry anything key-like."""
    from atlas.providers.redaction import redact

    for path in FIXTURES.rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert redact(text) == text, f"{path.name} contains a credential-shaped value"


def test_expectations_declare_the_contract_version():
    """A contract bump should force a conscious fixture review."""
    from atlas.planner.contracts import contract_version

    assert EXPECTATIONS["contractVersion"] == contract_version()
