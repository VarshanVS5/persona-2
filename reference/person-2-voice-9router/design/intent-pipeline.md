OWNER: Person 2 - Voice, Intent & 9Router - do not edit without owner approval

# Design: the intent pipeline and its trust boundary

## The one-line version

A user request becomes a *proposal*, never an instruction. Everything the model
produces is treated as hostile input until local code has checked it against
`packages/contracts/`.

## Flow

```
 voice audio                    typed text
     |                               |
     v                               v
 SpeechToTextProvider  ------>  normalize_request()        atlas.intent
     |  (typed error +               |  - bound length
     |   text fallback,              |  - strip control/zero-width/bidi chars
     |   never a task failure)       |  - NFKC + whitespace fold
     |                               |  - redact secrets (never sent to the LLM)
     |                               |  - flag injection markers (log only)
     +-------------------------------+
                                     |
                                     v
                           transcript event  --------------> UI
                                     |
                      +--------------+---------------+
                      |                              |
             ATLAS_MOCK_MODE=true            live 9Router configured
                      |                              |
                      v                              v
              FixturePlanner                   build_system_prompt()
           (registry-driven,                    from action-registry.json
            deterministic)                            |
                      |                               v
                      |                     NineRouterClient.complete()
                      |                      - bounded timeout
                      |                      - bounded retries + jitter
                      |                      - redacted logs
                      |                      - typed ProviderResult
                      |                               |
                      |                       raw assistant text
                      |                               |
   ===================|=========== TRUST BOUNDARY ====|=========================
                      |                               v
                      |                      JSON extraction (fence/prose
                      |                      tolerant, balanced-brace scan)
                      |                               |
                      |                      overwrite taskId + userRequest
                      |                      with locally held values
                      |                               |
                      +--------------+----------------+
                                     v
                             PlanValidator.validate()        atlas.planner
                              1. shape + step ceiling
                              2. task-plan.schema.json
                              3. identity match
                              4. per step: blocked category, registered
                                 action, argumentsSchema, tier equality,
                                 timeout ceiling, required condition types
                              5. step numbering, unique idempotency keys,
                                 consent not lowered, T3 refused
                                     |
                 +-------------------+--------------------+
                 v                   v                    v
              PLAN            CLARIFICATION           REJECTED
                 |                   |                    |
        plan_ready +         clarification_        provider_error
    confirmation_required      required            (only if a provider
                 |                                  actually failed)
                 v
        Person 1: policy engine -> consent -> executor -> verifier
        (authorization happens THERE; this gate is the first of two)
```

## Decisions and why

**The model never owns identity.** After parsing, `taskId` and `userRequest`
are overwritten from locally held values before validation. A model that
renamed the task or paraphrased the request would otherwise put different text
in front of the user than the user typed.

**Risk tier equality, not an upper bound.** A step's `riskTier` must *equal* the
registry's tier for that action. Allowing a *higher* declared tier might seem
conservative, but it would let the model inflate a tier to trigger a different
approval path, and it would make the plan disagree with the registry the
verifier uses. Master PRD 7: tiers are assigned by local code.

**T3 is refused outright.** Master PRD 7 permits T3 only behind "a separately
designed, explicit authorization flow". No such flow exists, so T3 is a
rejection rather than a stronger prompt.

**Confirmation threshold is T1, not T2.** Strict Master PRD 7 wording puts
explicit consent at T2. The only registered action (`app.open`) is T1 and
`packages/contracts/examples/plan.valid.json` sets `requiresConfirmation: true`
for it, so T1 is both the contract-consistent and the fail-safe reading. It
lives in one constant, `registry.CONFIRMATION_REQUIRED_FROM_TIER`. Raising it
to T2 is a deliberate relaxation and needs project-owner sign-off.

**Conditions are compared by type, not by arguments.** The registry stores
templates (`{"argument": "appId"}`); a plan carries materialised values
(`{"appId": "notepad"}`). Comparing arguments would reject the canonical valid
example. Evaluating condition arguments against real system state is the
verifier's job (Person 1), not the planner's.

**Blocked is checked before unknown.** `shell.exec` is both unregistered and
blocked. "ATLAS cannot run shell commands" is the message the user needs;
"unknown action type" is the message a developer needs. The user wins.

**Blocked actions are not re-prompted.** A recoverable formatting error gets one
repair attempt. A blocked or unregistered action does not: re-prompting after a
policy refusal is asking the model to find another way around the policy.

**The repair prompt contains only our error messages.** Feeding the model's own
rejected output back in would re-inject malformed (possibly adversarial)
content into the next prompt.

**No silent fix-ups.** If a candidate plan fails validation, it is rejected.
Locally patching it to pass would mean the user approves a plan the planner
never proposed, and would hide a real planning failure.

**Injection markers are logged, not enforced.** Phrase lists are trivially
bypassed. Two things actually defend the system: the request is delivered as a
JSON *string value* in a separate message (so it cannot terminate its own field
and continue as instructions), and whatever returns is validated regardless.
Marker detection exists so Person 4 and the audit journal can see the attempt.
`test_injected_instruction_in_the_request_cannot_produce_a_blocked_action`
proves the gate holds even when the model fully obeys an injected instruction.

**Secrets are redacted at the front door.** A pasted key is removed in
`normalize_request`, so it cannot reach the prompt, the plan, an event, or
Person 1's journal. The redacted text becomes `plan.userRequest`. This can
surprise a user who deliberately typed a key, which is the right trade: FR-14
and 8.3 both point the same way.

**Validation collects every problem.** First-failure-wins would make both the
repair prompt and Person 4's defect reports much weaker.

**A fixture plan is validated too.** Local origin is not trust. If the fixture
planner emits something non-conformant, that is an ATLAS defect and is reported
as one - it does not get a pass for being ours.

## What this module does not do

Authorization, allowlist resolution (`ATLAS_ALLOWED_APPS`/`ATLAS_ALLOWED_DIRS`),
execution, verification against real system state, journaling, task lifecycle,
and SSE emission. Those are Person 1's runtime and the project owner's
orchestrator. A valid plan from here has passed the *first* of two gates.
