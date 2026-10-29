"""The zero-dependency validator fallback.

OWNER: Person 2 - Voice, Intent & 9Router.

``services/atlas-core/pyproject.toml`` declares no dependencies and is owned by
the project owner, so plan validation must work whether or not ``jsonschema``
is installed. These tests cover both halves of that claim:

* :mod:`atlas.planner.jsonschema_lite` enforces the keyword subset our
  contracts use, and *raises* on a keyword it cannot enforce rather than
  passing the instance silently.
* A **subprocess with ``jsonschema`` import blocked** proves the real fallback
  path end to end - not a mocked approximation of it.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap

import pytest
from conftest import EXAMPLES_DIR, REPO_ROOT

from atlas.planner import jsonschema_lite
from atlas.planner.contracts import load_task_plan_schema
from atlas.planner.jsonschema_lite import UnsupportedSchemaFeature, validate

SRC = REPO_ROOT / "services" / "atlas-core" / "src"


# -- keyword coverage -----------------------------------------------------


def test_type_mismatch_is_reported():
    violations = validate("text", {"type": "object"})
    assert len(violations) == 1
    assert "expected type object" in violations[0].message


def test_booleans_do_not_satisfy_integer():
    """Python's ``bool`` is an ``int`` subclass; JSON Schema disagrees."""
    assert validate(True, {"type": "integer"})
    assert not validate(1, {"type": "integer"})


def test_booleans_do_not_satisfy_number():
    assert validate(False, {"type": "number"})


def test_integer_satisfies_number():
    assert not validate(7, {"type": "number"})


def test_union_types_are_supported():
    schema = {"type": ["string", "null"]}
    assert not validate("x", schema)
    assert not validate(None, schema)
    assert validate(5, schema)


def test_required_properties_are_reported_individually():
    violations = validate({}, {"type": "object", "required": ["a", "b"]})
    assert len(violations) == 2


def test_additional_properties_false_is_enforced():
    schema = {"type": "object", "properties": {"a": {"type": "string"}}, "additionalProperties": False}
    violations = validate({"a": "x", "b": 1}, schema)

    assert len(violations) == 1
    assert "'b'" in violations[0].message


def test_additional_properties_schema_is_applied():
    schema = {"type": "object", "properties": {}, "additionalProperties": {"type": "string"}}
    assert not validate({"x": "ok"}, schema)
    assert validate({"x": 1}, schema)


def test_enum_and_const():
    assert validate("T9", {"enum": ["T0", "T1"]})
    assert not validate("T1", {"enum": ["T0", "T1"]})
    assert not validate("1.0", {"const": "1.0"})
    assert validate("2.0", {"const": "1.0"})


def test_enum_distinguishes_true_from_one():
    """JSON equality, not Python equality."""
    assert validate(True, {"enum": [1]})
    assert not validate(1, {"enum": [1]})


def test_string_bounds_and_pattern():
    assert validate("", {"type": "string", "minLength": 1})
    assert validate("abcd", {"type": "string", "maxLength": 3})
    assert validate("xyz", {"type": "string", "pattern": "^sha256:"})
    assert not validate("sha256:abc", {"type": "string", "pattern": "^sha256:"})


def test_numeric_bounds():
    assert validate(0, {"type": "integer", "minimum": 1})
    assert not validate(1, {"type": "integer", "minimum": 1})
    assert validate(11, {"type": "integer", "maximum": 10})
    assert validate(1, {"exclusiveMinimum": 1})
    assert validate(1, {"exclusiveMaximum": 1})


def test_array_items_bounds_and_uniqueness():
    assert validate([1, "x"], {"type": "array", "items": {"type": "integer"}})
    assert validate([], {"type": "array", "minItems": 1})
    assert validate([1, 2, 3], {"type": "array", "maxItems": 2})
    assert validate([{"a": 1}, {"a": 1}], {"type": "array", "uniqueItems": True})
    assert not validate([{"a": 1}, {"a": 2}], {"type": "array", "uniqueItems": True})


def test_local_ref_is_resolved():
    schema = {
        "type": "object",
        "properties": {"tier": {"$ref": "#/$defs/Tier"}},
        "$defs": {"Tier": {"enum": ["T0", "T1"]}},
    }
    assert not validate({"tier": "T1"}, schema)
    assert validate({"tier": "T5"}, schema)


def test_violation_paths_locate_the_problem():
    schema = {
        "type": "object",
        "properties": {
            "steps": {"type": "array", "items": {"type": "object", "required": ["stepId"]}}
        },
    }
    violations = validate({"steps": [{"stepId": 1}, {}]}, schema)

    assert len(violations) == 1
    assert violations[0].path == "/steps/1"


def test_all_violations_are_collected():
    schema = {
        "type": "object",
        "required": ["a"],
        "properties": {"b": {"type": "integer"}},
        "additionalProperties": False,
    }
    violations = validate({"b": "x", "c": 1}, schema)
    assert len(violations) == 3


# -- unsupported keywords fail loudly ------------------------------------


@pytest.mark.parametrize("keyword", ["allOf", "anyOf", "oneOf", "if", "dependentSchemas"])
def test_unenforceable_keyword_raises_rather_than_passing(keyword):
    """Silently ignoring a keyword would mean "valid" was a lie."""
    with pytest.raises(UnsupportedSchemaFeature) as exc:
        validate({"a": 1}, {"type": "object", keyword: [{"type": "object"}]})

    assert keyword in str(exc.value)


def test_remote_ref_raises():
    with pytest.raises(UnsupportedSchemaFeature):
        validate({}, {"$ref": "task-plan.schema.json"})


def test_unresolvable_local_ref_raises():
    with pytest.raises(UnsupportedSchemaFeature):
        validate({}, {"$ref": "#/$defs/Missing", "$defs": {}})


def test_events_schema_is_known_to_be_out_of_scope():
    """``events.schema.json`` uses ``allOf``/``if``/``then``.

    Documents why event validation is reported as skipped under the fallback
    rather than quietly returning "valid".
    """
    from atlas.planner.contracts import load_events_schema

    with pytest.raises(UnsupportedSchemaFeature):
        validate({"schemaVersion": "1.0"}, load_events_schema())


# -- the real contract ---------------------------------------------------


def test_lite_validator_accepts_the_canonical_valid_plan():
    with (EXAMPLES_DIR / "plan.valid.json").open("r", encoding="utf-8") as handle:
        plan = json.load(handle)

    assert validate(plan, load_task_plan_schema()) == []


def test_lite_validator_rejects_an_additional_property():
    with (EXAMPLES_DIR / "plan.valid.json").open("r", encoding="utf-8") as handle:
        plan = json.load(handle)
    plan["executeNow"] = True

    assert validate(plan, load_task_plan_schema())


def test_task_plan_schema_stays_inside_the_supported_subset():
    """Early warning: if the owner adds ``allOf`` to the plan schema, the
    zero-dependency path stops working and this test says so.

    Asserting real violations (not just "did not raise") also proves the
    schema is being enforced rather than skipped.
    """
    violations = jsonschema_lite.validate({"taskId": "t"}, load_task_plan_schema())
    missing = {v.message for v in violations}

    assert any("'userRequest'" in m for m in missing)
    assert any("'summary'" in m for m in missing)
    assert any("'requiresConfirmation'" in m for m in missing)
    assert any("'steps'" in m for m in missing)


# -- end-to-end fallback (real subprocess, jsonschema blocked) -----------

_FALLBACK_SCRIPT = textwrap.dedent(
    """
    import sys, json

    class Blocker:
        def find_module(self, name, path=None):
            return self if name.split(".")[0] in ("jsonschema", "referencing") else None
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in ("jsonschema", "referencing"):
                raise ImportError("blocked for test")
            return None

    sys.meta_path.insert(0, Blocker())
    for mod in [m for m in sys.modules if m.split(".")[0] in ("jsonschema", "referencing")]:
        del sys.modules[mod]

    sys.path.insert(0, {src!r})

    from atlas.planner import PlanningService, validator_info
    from atlas.planner.validation import PlanValidator, RejectionCode

    info = validator_info()
    assert info.backend == "jsonschema_lite", info.backend
    assert info.events_supported is False

    service = PlanningService.for_mock_mode()
    result = service.plan_text("task-fallback-1", "open my editor")
    assert result.ok, result.describe()
    assert result.plan["steps"][0]["actionType"] == "app.open"
    assert result.plan_digest.startswith("sha256:")

    validator = PlanValidator()
    with open({examples!r} + "/plan.invalid-unknown-action.json", encoding="utf-8") as fh:
        blocked = json.load(fh)
    rejected = validator.validate(blocked)
    assert not rejected.valid
    assert rejected.has(RejectionCode.BLOCKED_ACTION)

    with open({examples!r} + "/plan.invalid-lowered-risk.json", encoding="utf-8") as fh:
        lowered = json.load(fh)
    assert validator.validate(lowered).has(RejectionCode.RISK_TIER_MISMATCH)

    bad = dict(result.plan)
    bad["unexpectedField"] = True
    assert validator.validate(bad).has(RejectionCode.SCHEMA_INVALID)

    print("FALLBACK_OK")
    """
)


def test_full_planning_works_with_jsonschema_uninstalled():
    """Runs the whole planning path in a subprocess where ``jsonschema`` and
    ``referencing`` cannot be imported at all."""
    script = _FALLBACK_SCRIPT.format(src=str(SRC), examples=str(EXAMPLES_DIR).replace("\\", "/"))
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=120,
    )

    assert completed.returncode == 0, completed.stderr
    assert "FALLBACK_OK" in completed.stdout
