OWNER: Person 2 - Voice, Intent & 9Router - do not edit without owner approval

# Fixtures published for Person 1 and Person 4

Plan fixtures and 9Router provider mocks, with the expected validator outcome
for each one recorded in [`expectations.json`](expectations.json).

These are not decorative. `services/atlas-core/tests/intent/test_published_fixtures.py`
replays every file through the real `PlanValidator` and the real
`NineRouterClient` response parser and asserts the declared outcome, so a
fixture cannot quietly stop reproducing its rejection after a contract or code
change.

## Layout

| Path | Contents |
|---|---|
| `plans/plan.valid.*.json` | Schema- and registry-conformant plans. `plan.valid.app-open.json` is byte-identical to what `FixturePlanner` emits for "open my editor", which is the demo path. |
| `plans/plan.invalid.*.json` | One plan per rejection reason. The filename names the reason; `expectations.json` names the exact `RejectionCode`. |
| `provider/9router.response.*.json` | OpenAI-compatible response bodies: valid plan, prose/fence-wrapped plan, clarification, malformed, HTTP-200-with-error, and a well-formed reply that reaches for a blocked action. |
| `expectations.json` | The machine-readable contract: file -> expected validity, expected rejection codes, risk tier, consent requirement, and why the case exists. |

## How to use them

Assert against `expectations.json` rather than hardcoding codes, so a contract
change updates your tests in one place:

```python
import json, pathlib
FIX = pathlib.Path("reference/person-2-voice-9router/fixtures")
expectations = json.loads((FIX / "expectations.json").read_text(encoding="utf-8"))

for case in expectations["plans"]:
    plan = json.loads((FIX / case["file"]).read_text(encoding="utf-8"))
    result = validator.validate(plan)
    assert result.valid is case["valid"]
```

To drive the planner from a provider fixture without a network, feed the
message content to `MockNineRouterClient`:

```python
body = json.loads((FIX / "provider/9router.response.valid-plan.json").read_text(encoding="utf-8"))
content = body["choices"][0]["message"]["content"]
planner = LlmPlanner(MockNineRouterClient(responses=[content]))
outcome = planner.plan("task-f001", "open my editor")
```

## Rules these fixtures respect

- Every `actionType` is either registered in
  `packages/contracts/action-registry.json` or is there precisely to be
  rejected. No fixture invents an action and expects it to work.
- No fixture contains a credential-shaped value; a test enforces this because
  the folder is committed.
- `expectations.json` pins `contractVersion`. When the project owner bumps the
  contract, the pin fails and the fixtures get a conscious review rather than a
  silent pass.
