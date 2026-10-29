OWNER: Person 2 - Voice, Intent & 9Router - do not edit without owner approval

# atlas.planner

Structured TaskPlan generation, JSON/schema validation, deterministic fixture planner.

## Entry point

```python
from atlas.planner import PlanningService

service = PlanningService.from_env()            # once, at startup
result = service.plan_text(task_id, user_request)

for event in result.events:                     # ordered AtlasEvent dicts
    publish(event)                              # SSE

if result.ok:
    plan = result.plan                          # schema-valid TaskPlan
    digest = result.plan_digest                 # POST /confirm must echo this
```

See `reference/person-2-voice-9router/docs/change-requests/20261029-project-owner-wire-planning-service.md`
for the orchestrator/API wiring request.

## Modules

| Module | Role |
|---|---|
| `service.py` | `PlanningService` - normalize -> plan -> validate -> events. The seam the orchestrator calls. |
| `base.py` | `Planner` protocol and `PlanOutcome` (`PLAN` / `CLARIFICATION` / `REJECTED`). |
| `validation.py` | `PlanValidator` - the local gate. Schema + action-registry conformance, closed `RejectionCode` taxonomy. |
| `registry.py` | Typed read-only view of `action-registry.json`; risk-tier ordering. |
| `contracts.py` | Loads `packages/contracts/`; selects `jsonschema` or the built-in fallback and reports which. |
| `jsonschema_lite.py` | Zero-dependency validator for the schema subset our contracts use. Raises on any keyword it cannot enforce. |
| `fixture_planner.py` | Deterministic offline planner for mock mode and the demo. |
| `llm_planner.py` | 9Router-backed planner: JSON extraction, bounded repair, no silent fix-ups. |
| `prompt.py` | Constrained prompt generated from the registry; request delivered as JSON data. |
| `events.py` | Builders for `transcript`, `plan_ready`, `clarification_required`, `confirmation_required`, `provider_error`. |
| `digest.py` | Canonical `sha256:` plan digest that binds a confirmation to the reviewed plan. |

## Boundaries

A plan returned from here has passed `task-plan.schema.json` and
action-registry conformance. It is **not authorized to execute**. Allowlist
resolution, consent state, execution, verification, and journaling belong to
Person 1 (`policy/`, `executor/`, `adapters/`, `verifier/`, `journal/`). This is
the first of two gates.

`packages/contracts/` is read-only here. Person 2 may not invent action types
or permissions; every allowlist, tier, and timeout is read from the registry.

## Tests

```powershell
python -m pytest services/atlas-core/tests/intent -q
```
