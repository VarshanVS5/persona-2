"""Dependency-free validator for the JSON Schema subset used by our contracts.

OWNER: Person 2 - Voice, Intent & 9Router.

Why this exists: ``services/atlas-core/pyproject.toml`` is owned by the project
owner and declares no dependencies, so this workstream must validate plans
without requiring ``jsonschema`` to be installed. When ``jsonschema`` *is*
available, :mod:`atlas.planner.schema` prefers it; this module is the fallback
so that schema validation is never silently skipped.

**Scope, stated precisely.** This implements only the keywords that
``packages/contracts/task-plan.schema.json`` and the ``argumentsSchema``
blocks of ``packages/contracts/action-registry.json`` actually use:

``type`` (object/array/string/integer/number/boolean/null, and lists thereof),
``required``, ``properties``, ``additionalProperties`` (boolean form),
``items``, ``enum``, ``const``, ``minimum``, ``maximum``, ``minLength``,
``maxLength``, ``minItems``, ``maxItems``, ``pattern``, ``uniqueItems``, and
local ``$ref`` of the form ``#/$defs/Name``.

It does **not** implement ``allOf``/``anyOf``/``oneOf``/``if``/``then``,
``$ref`` to another file, ``format`` assertions, or ``dependentSchemas``. An
unsupported keyword is *reported*, never ignored: :func:`validate` raises
:class:`UnsupportedSchemaFeature` so that a contract change which outgrows this
validator fails loudly instead of silently passing everything. This matters
because ``events.schema.json`` *does* use ``allOf``/``if``/``then``; event
validation therefore requires real ``jsonschema`` and is skipped (and reported
as skipped) otherwise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_SUPPORTED_KEYWORDS = frozenset(
    {
        "$ref",
        "$schema",
        "$id",
        "$defs",
        "title",
        "description",
        "x-contract-version",
        "default",
        "examples",
        "type",
        "required",
        "properties",
        "additionalProperties",
        "items",
        "enum",
        "const",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "pattern",
        "uniqueItems",
    }
)

_TYPE_CHECKS: dict[str, Any] = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    # JSON has no bool/int distinction problem, Python does: bool is a subclass
    # of int, so `True` must not satisfy `"type": "integer"`.
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


class UnsupportedSchemaFeature(Exception):
    """Raised when the schema uses a keyword this validator cannot enforce."""


@dataclass(frozen=True)
class SchemaViolation:
    """One validation failure, located by JSON Pointer-ish path."""

    path: str
    message: str

    def __str__(self) -> str:
        where = self.path or "(root)"
        return f"{where}: {self.message}"


def validate(instance: Any, schema: dict[str, Any]) -> list[SchemaViolation]:
    """Return all violations of ``instance`` against ``schema`` (empty = valid).

    Raises :class:`UnsupportedSchemaFeature` if the schema uses a keyword
    outside the documented subset.
    """
    violations: list[SchemaViolation] = []
    _validate(instance, schema, schema, "", violations)
    return violations


def _resolve(schema: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    ref = schema.get("$ref")
    if not isinstance(ref, str):
        return schema
    if not ref.startswith("#/"):
        raise UnsupportedSchemaFeature(
            f"only local '#/...' refs are supported by jsonschema_lite, got {ref!r}"
        )
    node: Any = root
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            raise UnsupportedSchemaFeature(f"unresolvable $ref {ref!r}")
        node = node[part]
    if not isinstance(node, dict):
        raise UnsupportedSchemaFeature(f"$ref {ref!r} does not point at a schema object")
    # A $ref sibling may itself be a $ref; resolve transitively.
    return _resolve(node, root)


def _check_supported(schema: dict[str, Any]) -> None:
    unknown = sorted(set(schema) - _SUPPORTED_KEYWORDS)
    if unknown:
        raise UnsupportedSchemaFeature(
            "jsonschema_lite cannot enforce keyword(s) "
            + ", ".join(repr(k) for k in unknown)
            + "; install 'jsonschema' for full validation"
        )


def _join(path: str, part: str | int) -> str:
    return f"{path}/{part}" if path else f"/{part}"


def _validate(
    instance: Any,
    schema: dict[str, Any],
    root: dict[str, Any],
    path: str,
    out: list[SchemaViolation],
) -> None:
    schema = _resolve(schema, root)
    _check_supported(schema)

    # -- type ---------------------------------------------------------------
    expected = schema.get("type")
    if expected is not None:
        names = expected if isinstance(expected, list) else [expected]
        for name in names:
            if name not in _TYPE_CHECKS:
                raise UnsupportedSchemaFeature(f"unknown type {name!r}")
        if not any(_TYPE_CHECKS[name](instance) for name in names):
            out.append(
                SchemaViolation(path, f"expected type {'/'.join(names)}, got {_type_name(instance)}")
            )
            # Further keyword checks would be meaningless on the wrong type.
            return

    # -- enum / const -------------------------------------------------------
    if "enum" in schema:
        allowed = schema["enum"]
        if not _contains_equal(allowed, instance):
            out.append(SchemaViolation(path, f"value {instance!r} is not one of {allowed!r}"))
    if "const" in schema and not _equal(schema["const"], instance):
        out.append(SchemaViolation(path, f"value {instance!r} must equal {schema['const']!r}"))

    # -- strings ------------------------------------------------------------
    if isinstance(instance, str):
        min_length = schema.get("minLength")
        if isinstance(min_length, int) and len(instance) < min_length:
            out.append(SchemaViolation(path, f"string shorter than minLength {min_length}"))
        max_length = schema.get("maxLength")
        if isinstance(max_length, int) and len(instance) > max_length:
            out.append(SchemaViolation(path, f"string longer than maxLength {max_length}"))
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, instance) is None:
            out.append(SchemaViolation(path, f"string does not match pattern {pattern!r}"))

    # -- numbers ------------------------------------------------------------
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        minimum = schema.get("minimum")
        if isinstance(minimum, (int, float)) and instance < minimum:
            out.append(SchemaViolation(path, f"value {instance} is below minimum {minimum}"))
        maximum = schema.get("maximum")
        if isinstance(maximum, (int, float)) and instance > maximum:
            out.append(SchemaViolation(path, f"value {instance} is above maximum {maximum}"))
        excl_min = schema.get("exclusiveMinimum")
        if isinstance(excl_min, (int, float)) and instance <= excl_min:
            out.append(
                SchemaViolation(path, f"value {instance} must be greater than {excl_min}")
            )
        excl_max = schema.get("exclusiveMaximum")
        if isinstance(excl_max, (int, float)) and instance >= excl_max:
            out.append(SchemaViolation(path, f"value {instance} must be less than {excl_max}"))

    # -- objects ------------------------------------------------------------
    if isinstance(instance, dict):
        properties = schema.get("properties")
        properties = properties if isinstance(properties, dict) else {}

        required = schema.get("required")
        if isinstance(required, list):
            for key in required:
                if key not in instance:
                    out.append(SchemaViolation(path, f"missing required property {key!r}"))

        additional = schema.get("additionalProperties")
        if additional is False:
            for key in instance:
                if key not in properties:
                    out.append(SchemaViolation(path, f"additional property {key!r} is not allowed"))
        elif isinstance(additional, dict):
            for key, value in instance.items():
                if key not in properties:
                    _validate(value, additional, root, _join(path, key), out)

        for key, subschema in properties.items():
            if key in instance and isinstance(subschema, dict):
                _validate(instance[key], subschema, root, _join(path, key), out)

    # -- arrays -------------------------------------------------------------
    if isinstance(instance, list):
        min_items = schema.get("minItems")
        if isinstance(min_items, int) and len(instance) < min_items:
            out.append(SchemaViolation(path, f"array shorter than minItems {min_items}"))
        max_items = schema.get("maxItems")
        if isinstance(max_items, int) and len(instance) > max_items:
            out.append(SchemaViolation(path, f"array longer than maxItems {max_items}"))
        if schema.get("uniqueItems") is True and _has_duplicates(instance):
            out.append(SchemaViolation(path, "array items must be unique"))

        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(instance):
                _validate(item, items, root, _join(path, index), out)


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _equal(left: Any, right: Any) -> bool:
    """JSON equality: 1 and True are distinct even though Python says otherwise."""
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_equal(left[k], right[k]) for k in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    return left == right


def _contains_equal(candidates: Any, value: Any) -> bool:
    return isinstance(candidates, list) and any(_equal(c, value) for c in candidates)


def _has_duplicates(items: list[Any]) -> bool:
    for index, item in enumerate(items):
        for other in items[index + 1 :]:
            if _equal(item, other):
                return True
    return False
