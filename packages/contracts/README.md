OWNER: Project owner (lead / integration owner) - do not edit without owner approval

# packages/contracts - shared cross-team interfaces

Every workstream codes against these files and never against another workstream's internals.
Rules and the change procedure are in [INSTRUCTIONS.md](INSTRUCTIONS.md). Version history is in [CHANGELOG.md](CHANGELOG.md).

| File | Content | Producer | Consumers |
|---|---|---|---|
| `task-plan.schema.json` | `TaskPlan`, `ActionStep`, `Condition`, `RiskTier` (T0-T3), Common PRD 4.1 | Person 2 emits plans | Person 1 validates/executes, Person 3 renders, Person 4 tests |
| `action-registry.json` | Registered actions (`app.open`) and the `blocked` categories from Master PRD section 7 | Project owner (Person 1 proposes additions via PR) | Person 1 enforces, Person 2 constrains prompts to it, Person 4 tests |
| `events.schema.json` | Event envelope `{schemaVersion, taskId, timestamp, type, payload}`, the 11 event types, the 10 `TaskStatus` values | Backend orchestrator (runtime and planner emit via it) | Person 3 maps to UI/character states, Person 4 asserts |
| `examples/` | Fixtures for mocks and tests | Project owner | All |
| `../../docs/api-contract.md` | REST surface and event stream | Project owner | All |

## Examples and expected outcomes

| Example | Expected result |
|---|---|
| `examples/plan.valid.json` | Schema-valid; every `actionType` is registered; may proceed to confirmation |
| `examples/plan.invalid-unknown-action.json` | Schema-valid but REJECTED: `shell.exec` is not in `action-registry.json` (and is a blocked category) |
| `examples/plan.invalid-lowered-risk.json` | Schema-valid but REJECTED: declares `T0` for `app.open`, which the registry registers as `T1`; a plan may never lower a registered tier |
| `examples/event.plan_ready.json` | Valid `plan_ready` event |
| `examples/event.task_completed.json` | Valid `task_completed` event (`SUCCEEDED`) |
| `examples/event.task_failed.json` | Valid `task_failed` event (`FAILED`, failed step 1) |

The invalid plans are deliberately schema-valid so that tests prove the registry and risk checks, not the JSON Schema, reject them.
