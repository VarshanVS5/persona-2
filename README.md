# Project ATLAS - Persona 2: Voice, Intent & 9Router

Person 2's workstream from Project ATLAS (Autonomous Task Layer for Application
Systems), a Windows desktop operator that turns a natural-language request into
a validated, approval-gated action plan.

This repository holds **only Persona 2's deliverables**, at their original
monorepo-relative paths so they can be merged back into the main repository
without moving anything.

## Scope

Persona 2 owns intent interpretation, structured planning, input normalization,
9Router integration, and the speech-provider interface. It produces plans; it
never executes them. Execution, policy authorization, verification, and
journaling belong to Persona 1.

## What is here

| Path | Contents | Owner |
|---|---|---|
| `services/atlas-core/src/atlas/intent/` | Input normalization, voice intake with text fallback | Persona 2 |
| `services/atlas-core/src/atlas/planner/` | Plan validation, action registry view, fixture planner, 9Router planner, prompt, events, digest, `PlanningService` | Persona 2 |
| `services/atlas-core/src/atlas/providers/` | 9Router client, secret redaction, provider settings, AWS Transcribe/Polly interfaces | Persona 2 |
| `services/atlas-core/tests/intent/` | 413 tests | Persona 2 |
| `reference/person-2-voice-9router/` | Instructions, acceptance checklist, completion report, design notes, fixtures, change requests | Persona 2 |
| `workstreams/person-2-voice-9router/` | Pointer files for the canonical workstream path | Persona 2 |
| `packages/contracts/` | **Read-only dependency, NOT owned by Persona 2.** Included because the planner refuses to start without the action registry and plan schema. Owned by the project owner in the main repository; do not edit here. | Project owner |

## Quick start

Requires Python 3.11+. No third-party dependency is needed - the module runs on
the standard library alone. `jsonschema` is used for full draft 2020-12
validation when installed, and a built-in validator covers task plans when it
is not.

```powershell
# Run the test suite
python -m pytest services/atlas-core/tests/intent -q

# Plan a request offline (no credentials, no network)
$env:PYTHONPATH="services/atlas-core/src"
python -c "from atlas.planner import PlanningService; s=PlanningService.for_mock_mode(); r=s.plan_text('task-0001','open my editor'); print(r.describe()); print([e['type'] for e in r.events])"
```

Expected output from the second command:

```
PLAN steps=1 tier=T1 confirm=True mocked=True digest=sha256:bc023e5b3f59... | source=text chars=14 originalChars=14 | events=3
['transcript', 'plan_ready', 'confirmation_required']
```

## Using it from the backend

```python
from atlas.planner import PlanningService

service = PlanningService.from_env()              # once, at startup
result = service.plan_text(task_id, user_request)

for event in result.events:                       # ordered AtlasEvent dicts
    publish(event)                                # SSE to the UI

if result.ok:
    plan = result.plan                            # schema-valid TaskPlan
    digest = result.plan_digest                   # POST /confirm must echo this
```

`service.verify_plan_digest(plan, submitted_digest)` implements the rule that a
confirmation applies only to the exact plan the user reviewed.

## The security model in one paragraph

The LLM is a planner, not an authority. Model output is treated as hostile and
passes five local validation layers before it can become an action: shape and
step ceiling; the `TaskPlan` JSON schema including `additionalProperties:
false`; identity match against locally held `taskId`/`userRequest`; per-step
registry conformance (blocked category, registered action, argument schema,
risk-tier equality, timeout ceiling, required condition types); and plan-level
invariants (contiguous step numbering, unique idempotency keys, consent never
lowered, tier T3 refused outright). The model cannot reassign a risk tier,
waive user confirmation, raise a timeout, invent an action, or choose the task
identity. Blocked and unknown actions are refused without a retry, so a refusal
never becomes a prompt for a workaround. Secrets are stripped from the request
before it reaches the model, and provider failures are typed errors that can
never read as task success.

See `reference/person-2-voice-9router/design/intent-pipeline.md` for the full
flow and the reasoning behind each decision.

## Status

**412 passed, 1 skipped** in this repository. Six of six Common PRD acceptance
items for Persona 2 are met.

The one skip is expected here and is not a gap:
`test_nine_router.py::test_no_frontend_file_references_the_api_key` is a
structural guard that fails if any file under `apps/desktop-ui/` mentions a
9Router credential or endpoint. Persona 3 owns that tree and it is not part of
this repository, so the test reports `apps/desktop-ui is not present` and
skips. In the full monorepo it runs and passes (413 passed, 0 skipped).

Honest limitations, detailed in
`reference/person-2-voice-9router/COMPLETION-REPORT.md`:

- **Live 9Router is unverified.** The client is implemented against the
  documented OpenAI-compatible `/v1/chat/completions` contract and verified
  against a local stub HTTP server. No real gateway has been contacted, because
  no endpoint or credential exists.
- **Live Amazon Transcribe and Polly are not implemented.** Both providers are
  real gates, not stubs that pretend to work: they return typed
  `DISABLED`/`NOT_CONFIGURED` errors, report `live_call_implemented = False`,
  and contain no code path that contacts AWS. Voice input works only through
  the offline mock; text input is always available.
- **Event-schema validation is skipped under the zero-dependency fallback**,
  because `events.schema.json` uses `allOf`/`if`/`then`. It reports itself as
  skipped rather than as valid.
- The orchestrator, HTTP API, Persona 1's runtime, and Persona 3's UI live in
  the main repository and are still placeholders there, so this module is
  complete and tested but not yet reachable from the desktop application.

## License

MIT - see [LICENSE](LICENSE).
