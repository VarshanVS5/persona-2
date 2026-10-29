OWNER: Person 2 - Voice, Intent & 9Router (change request addressed to the project owner)

# Change request: wire `PlanningService` into the orchestrator and API

- **Date:** 2026-10-29
- **Requested by:** Person 2 - Voice, Intent & 9Router
- **Target owner:** Project owner
- **Target paths:** `services/atlas-core/src/atlas/orchestrator/`, `services/atlas-core/src/atlas/api/`, `services/atlas-core/src/atlas/main.py`
- **Procedure:** `reference/person-2-voice-9router/INSTRUCTIONS.md` section 9 - I have not edited these files.

## Why

Person 2's module is complete and tested behind a stable interface, but the
orchestrator and API are project-owner owned and are still one-line
placeholders. Nothing calls the planner yet, so the backend half of
`POST /api/v1/tasks/plan` does not exist end to end.

## What I am asking for

Call the already-built entry point. No new dependencies, no contract changes.

```python
from atlas.planner import PlanningService

# Once, at startup.
planning = PlanningService.from_env()

# POST /api/v1/tasks/plan
result = planning.plan_text(task_id, body["userRequest"])
for event in result.events:          # ordered, schema-valid AtlasEvent dicts
    event_bus.publish(event)         # SSE: GET /api/v1/tasks/{taskId}/events

if result.ok:
    plan = result.plan               # schema-valid TaskPlan
    digest = result.plan_digest      # must be echoed by POST /confirm
    # -> hand to Person 1's policy engine, then await confirmation
else:
    # result.outcome.kind is CLARIFICATION or REJECTED; the events already
    # describe it. Nothing was executed.
    ...
```

For `POST /api/v1/tasks/{taskId}/confirm`, the digest rule in
`docs/api-contract.md` is implemented already, so it does not need
reimplementing:

```python
if not planning.verify_plan_digest(stored_plan, body["planDigest"]):
    return 409, {"error": "PLAN_CHANGED"}   # the reviewed plan no longer matches
```

For `GET /api/v1/health`, `planning.mock_mode` is the value the
`mockMode` field needs, and `planning.diagnostics()` returns a log-safe status
dictionary (no secret values, only presence flags) suitable for a diagnostics
screen.

## Interface guarantees you can rely on

- `plan_text` / `plan_voice` never raise for provider or model failures; every
  failure is a typed outcome plus events. The only exception raised is
  `ValueError` for a missing `task_id`, which is a programming error.
- A `PLAN` outcome has already passed `task-plan.schema.json` and
  action-registry conformance. It is **not** authorized - Person 1's policy
  engine is still the authorization boundary.
- Events are built from `packages/contracts/events.schema.json` and are
  redacted before return.
- With `ATLAS_MOCK_MODE=true` or no `NINE_ROUTER_*` values, planning is fully
  offline and deterministic, so the API can be exercised with no credentials.

## Impact if declined

Person 2's module stays independently testable (370 passing tests), but the
UI-to-planner path cannot be demonstrated through the real API and the vertical
slice stays incomplete.
