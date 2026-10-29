"""Loading and validating against ``packages/contracts/``.

OWNER: Person 2 - Voice, Intent & 9Router.

``packages/contracts/`` is project-owner owned and read-only from here. This
module is the single place that reads those files, so the rest of the planner
never hardcodes an action type, a risk tier, or a schema rule.

Validator selection, and why it is reported rather than hidden:

* If ``jsonschema`` is importable it is used (full draft 2020-12 support,
  including the ``allOf``/``if``/``then`` blocks in ``events.schema.json``).
* Otherwise :mod:`atlas.planner.jsonschema_lite` validates task plans, and
  event-envelope validation is **skipped and reported as skipped** rather than
  silently passing. :func:`validator_info` exposes which mode is active so a
  diagnostics screen can state it.

A plan that passes schema validation is still not authorized to run: risk-tier,
argument, and allowlist checks live in :mod:`atlas.planner.validation`, and
execution authorization belongs to Person 1's policy engine.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import jsonschema_lite
from .jsonschema_lite import SchemaViolation, UnsupportedSchemaFeature

try:  # pragma: no cover - import-availability branch, covered by both CI modes
    import jsonschema as _jsonschema

    _HAVE_JSONSCHEMA = True
except ImportError:  # pragma: no cover
    _jsonschema = None  # type: ignore[assignment]
    _HAVE_JSONSCHEMA = False


class ContractsUnavailable(RuntimeError):
    """Raised when ``packages/contracts/`` cannot be located or parsed.

    Deliberately fatal: a backend that cannot read the action registry must not
    start planning. Guessing an allowlist would be the exact failure mode the
    policy model exists to prevent.
    """


def _repo_root() -> Path:
    # .../services/atlas-core/src/atlas/planner/contracts.py
    #  parents: [planner, atlas, src, atlas-core, services, <repo root>]
    return Path(__file__).resolve().parents[5]


def contracts_dir() -> Path:
    """Directory holding the shared contracts.

    ``ATLAS_CONTRACTS_DIR`` overrides the default so tests (and a packaged
    build, where the layout differs) can point at a copy.
    """
    override = os.environ.get("ATLAS_CONTRACTS_DIR")
    if override and override.strip():
        return Path(override.strip()).expanduser().resolve()
    return _repo_root() / "packages" / "contracts"


def _load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise ContractsUnavailable(f"contract file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ContractsUnavailable(f"contract file {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ContractsUnavailable(f"contract file {path} must contain a JSON object")
    return data


# ``lru_cache`` keyed on the resolved path: contracts are immutable at runtime,
# and re-reading three files per planning request would be wasteful. Tests that
# change ATLAS_CONTRACTS_DIR get a different key, and may call
# ``clear_contract_cache()``.
@lru_cache(maxsize=8)
def _cached_json(path_str: str) -> dict[str, Any]:
    return _load_json(Path(path_str))


def clear_contract_cache() -> None:
    """Drop cached contract files (test isolation / config reload)."""
    _cached_json.cache_clear()


def load_task_plan_schema() -> dict[str, Any]:
    return _cached_json(str(contracts_dir() / "task-plan.schema.json"))


def load_events_schema() -> dict[str, Any]:
    return _cached_json(str(contracts_dir() / "events.schema.json"))


def load_action_registry() -> dict[str, Any]:
    return _cached_json(str(contracts_dir() / "action-registry.json"))


def contract_version() -> str:
    """``x-contract-version`` of the task-plan schema, for logs and diagnostics."""
    value = load_task_plan_schema().get("x-contract-version")
    return value if isinstance(value, str) else "unknown"


@dataclass(frozen=True)
class ValidatorInfo:
    """Which validator is active, and what it can enforce."""

    backend: str
    events_supported: bool

    def describe(self) -> str:
        if self.backend == "jsonschema":
            return "jsonschema (full draft 2020-12)"
        return (
            "built-in jsonschema_lite subset; task plans are fully validated, "
            "event-envelope conditional subschemas are NOT validated "
            "(install 'jsonschema' to enable)"
        )


def validator_info() -> ValidatorInfo:
    if _HAVE_JSONSCHEMA:
        return ValidatorInfo(backend="jsonschema", events_supported=True)
    return ValidatorInfo(backend="jsonschema_lite", events_supported=False)


def validate_against_schema(instance: Any, schema: dict[str, Any]) -> list[SchemaViolation]:
    """Validate ``instance``, returning every violation found.

    Uses ``jsonschema`` when available, otherwise the built-in subset. Both
    paths return the same :class:`SchemaViolation` list, so callers do not
    branch on the backend.
    """
    if _HAVE_JSONSCHEMA:
        validator_cls = _jsonschema.validators.validator_for(schema)  # type: ignore[union-attr]
        validator = validator_cls(schema, registry=_schema_registry())
        return [
            SchemaViolation(
                path="/" + "/".join(str(part) for part in error.absolute_path),
                message=error.message,
            )
            for error in sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
        ]
    return jsonschema_lite.validate(instance, schema)


def _schema_registry() -> Any:
    """Referencing registry so ``events.schema.json`` can resolve its sibling.

    ``PlanReadyPayload.plan`` uses ``{"$ref": "task-plan.schema.json"}``, a
    relative file reference. Without a registry, ``jsonschema`` would try to
    fetch it over the network.
    """
    from referencing import Registry, Resource  # imported lazily: ships with jsonschema>=4.18

    plan_schema = load_task_plan_schema()
    events_schema = load_events_schema()
    resources = [
        ("task-plan.schema.json", plan_schema),
        (plan_schema.get("$id", ""), plan_schema),
        ("events.schema.json", events_schema),
        (events_schema.get("$id", ""), events_schema),
    ]
    registry = Registry()
    for uri, schema in resources:
        if uri:
            registry = registry.with_resource(uri, Resource.from_contents(schema))
    return registry


def validate_task_plan_schema(plan: Any) -> list[SchemaViolation]:
    """Schema-only validation of a task plan (no registry/policy rules)."""
    return validate_against_schema(plan, load_task_plan_schema())


def validate_event(event: Any) -> list[SchemaViolation]:
    """Validate an :class:`AtlasEvent` envelope.

    Returns ``[]`` when validation is unavailable; call :func:`validator_info`
    to find out whether ``[]`` means "valid" or "not checked". The planner
    builds events from typed constructors, so this is a verification aid rather
    than the primary defence.
    """
    if not validator_info().events_supported:
        return []
    try:
        return validate_against_schema(event, load_events_schema())
    except UnsupportedSchemaFeature:  # pragma: no cover - defensive
        return []
