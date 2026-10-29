OWNER: Person 2 - Voice, Intent & 9Router - do not edit without owner approval

# Completion report - Person 2 - Voice, Intent & 9Router

Date: 2026-10-29. Scope: intent interpretation, structured planning, input
normalization, 9Router integration, and the agreed speech-provider interface.

## 1. Files created or modified

### Production code (paths owned by Person 2)

| File | Lines | Purpose |
|---|---|---|
| `services/atlas-core/src/atlas/intent/__init__.py` | 24 | Package surface |
| `services/atlas-core/src/atlas/intent/normalize.py` | 205 | Input normalization, secret stripping, injection-marker flagging |
| `services/atlas-core/src/atlas/intent/voice.py` | 160 | `VoiceIntake`: transcript capture with guaranteed text fallback |
| `services/atlas-core/src/atlas/intent/README.md` | 45 | Module docs (replaced the one-line placeholder) |
| `services/atlas-core/src/atlas/planner/__init__.py` | 105 | Package surface, `PlanningService` entry point |
| `services/atlas-core/src/atlas/planner/base.py` | 155 | `Planner` protocol, `PlanOutcome` (PLAN/CLARIFICATION/REJECTED) |
| `services/atlas-core/src/atlas/planner/validation.py` | 420 | `PlanValidator` - the local gate; closed `RejectionCode` taxonomy |
| `services/atlas-core/src/atlas/planner/registry.py` | 215 | Typed read-only view of `action-registry.json` |
| `services/atlas-core/src/atlas/planner/contracts.py` | 215 | Contract loading; validator selection and reporting |
| `services/atlas-core/src/atlas/planner/jsonschema_lite.py` | 290 | Zero-dependency JSON Schema subset validator |
| `services/atlas-core/src/atlas/planner/fixture_planner.py` | 175 | Deterministic offline planner (mock mode, demo) |
| `services/atlas-core/src/atlas/planner/llm_planner.py` | 310 | 9Router-backed planner; JSON extraction, bounded repair |
| `services/atlas-core/src/atlas/planner/prompt.py` | 150 | Constrained prompt generated from the registry |
| `services/atlas-core/src/atlas/planner/events.py` | 200 | Builders for the four events Person 3 consumes |
| `services/atlas-core/src/atlas/planner/digest.py` | 65 | Canonical `sha256:` plan digest |
| `services/atlas-core/src/atlas/planner/service.py` | 320 | `PlanningService` - the orchestrator-facing seam |
| `services/atlas-core/src/atlas/planner/README.md` | 60 | Module docs (replaced the one-line placeholder) |
| `services/atlas-core/src/atlas/providers/__init__.py` | 65 | Package surface |
| `services/atlas-core/src/atlas/providers/base.py` | 215 | `ProviderResult`/`ProviderError`/`ProviderErrorCode`, protocols |
| `services/atlas-core/src/atlas/providers/settings.py` | 180 | Provider config from `.env` keys; fail-safe defaults |
| `services/atlas-core/src/atlas/providers/redaction.py` | 160 | Two-layer secret redaction |
| `services/atlas-core/src/atlas/providers/nine_router.py` | 435 | 9Router client + `MockNineRouterClient` |
| `services/atlas-core/src/atlas/providers/aws_transcribe.py` | 180 | Speech-to-text interface, gate, and mock |
| `services/atlas-core/src/atlas/providers/aws_polly.py` | 155 | Text-to-speech interface, gate, and mock |
| `services/atlas-core/src/atlas/providers/README.md` | 65 | Module docs with honest per-module status |

### Tests (`services/atlas-core/tests/intent/`, owned by Person 2)

`conftest.py`, `test_validation.py`, `test_nine_router.py`,
`test_llm_planner.py`, `test_fixture_planner.py`, `test_planning_service.py`,
`test_events_and_digest.py`, `test_normalize.py`, `test_redaction.py`,
`test_speech_providers.py`, `test_settings_and_registry.py`,
`test_jsonschema_lite.py`, `test_published_fixtures.py`.

### Working folder (`reference/person-2-voice-9router/`)

`acceptance-checklist.md` (rewritten with evidence), `COMPLETION-REPORT.md`
(this file), `design/intent-pipeline.md`, `fixtures/README.md`,
`fixtures/expectations.json`, 10 plan fixtures under `fixtures/plans/`,
6 provider fixtures under `fixtures/provider/`, and 3 change requests under
`docs/change-requests/`.

### Pointer files (`workstreams/person-2-voice-9router/`, an owned path)

`README.md`, `INSTRUCTIONS.md`, `acceptance-checklist.md`, `prd/README.md`, and
three `.gitkeep` files. These exist only because `scripts/verify-repo.ps1`
requires the canonical workstream path that the ownership map records; they
point at `reference/person-2-voice-9router/` rather than duplicating it.

### Files NOT modified

No file outside Person 2's owned paths was touched. Specifically **not** edited:
`packages/contracts/**`, `docs/**`, `.env.example`, `scripts/**`, `.github/**`,
`OWNERSHIP.json`, `services/atlas-core/pyproject.toml`,
`src/atlas/{config.py,main.py,api/,models/,orchestrator/,audit/}`,
`tests/conftest.py`, `apps/desktop-ui/**`, and the other personas' paths. Three
change requests were filed instead.

## 2. Features actually implemented

**Input normalization** (`atlas.intent.normalize`). Length ceiling, control /
zero-width / bidirectional character stripping, NFKC + whitespace folding,
secret redaction before anything leaves the process, injection-marker flagging
for the audit trail, and `needs_clarification` instead of a guessed plan for
blank, too-short, or content-free input.

**Voice intake** (`atlas.intent.voice`). Wraps any `SpeechToTextProvider`,
normalizes the transcript, and guarantees a text fallback: no input - including
a provider that raises - produces a task failure.

**Plan validation** (`atlas.planner.validation`). Five layers: shape and step
ceiling; `task-plan.schema.json` (including `additionalProperties: false`);
identity match against locally held `taskId`/`userRequest`; per-step registry
conformance (blocked category, registered action, `argumentsSchema`, risk-tier
equality, timeout ceiling, required condition types); and plan-level invariants
(contiguous step numbering, unique idempotency keys, consent never lowered, T3
refused). 20 named rejection codes, all collected rather than first-failure.

**Action registry view** (`atlas.planner.registry`). Read-only, structurally
validated. A malformed or empty registry is fatal rather than worked around.
Everything downstream - prompt catalogue, allowlist, tiers, timeouts - derives
from it, so adding an action needs no change in this workstream.

**Schema validation with a zero-dependency fallback**
(`atlas.planner.contracts`, `jsonschema_lite`). Uses `jsonschema` when
installed; otherwise a built-in validator for the keyword subset our contracts
use, which **raises** on any keyword it cannot enforce rather than passing
silently. Event-envelope validation is reported as skipped under the fallback,
not reported as valid.

**Deterministic fixture planner** (`atlas.planner.fixture_planner`). Builds
plans entirely from registry metadata, passes through the same validator as
live output, and labels every result as mocked. This is the demo and
mock-mode path, and it needs no credentials or network.

**9Router client** (`atlas.providers.nine_router`). OpenAI-compatible
`POST /v1/chat/completions` over stdlib `urllib` (no new dependency), with a
bounded timeout, bounded retries with capped jittered backoff, request and
response byte ceilings, a full HTTP error taxonomy, HTTP-200-with-error
detection, token-usage capture for budget accounting, and redacted logging.

**LLM planner** (`atlas.planner.llm_planner`). JSON extraction tolerant of
markdown fences and surrounding prose via a string-aware balanced-brace scan;
identity overwritten before validation; at most two provider calls per request;
repair prompts built only from our own rejection messages; blocked and unknown
actions refused without a retry; no silent fix-ups.

**Constrained prompt** (`atlas.planner.prompt`). Action catalogue and blocked
list generated from the registry; the user request delivered as a JSON string
value in a separate message.

**Plan digest** (`atlas.planner.digest`). Canonical-JSON `sha256:` digest with a
constant-time comparison helper, so a confirmation can be bound to the exact
plan the user reviewed.

**Event builders** (`atlas.planner.events`). `transcript`, `plan_ready`,
`confirmation_required`, `clarification_required`, `provider_error`, built to
`events.schema.json` with RFC 3339 `Z` timestamps and redacted payloads. A
local validation rejection is deliberately *not* reported as a provider error.

**Speech provider interfaces** (`aws_transcribe`, `aws_polly`). Interface plus
a working offline mock for each. The live AWS path is a real gate (see §7).

**`PlanningService`** (`atlas.planner.service`). One call from request to
ordered events, plus `diagnostics()` for a settings screen and
`verify_plan_digest()` for the confirm handler.

**Secret redaction** (`atlas.providers.redaction`). Exact-value redaction of
every credential the process holds (registered automatically where settings
load) plus credential-shape patterns. `ProviderError` redacts its message at
construction.

## 3. How this integrates with the Windows desktop application

ATLAS is a Tauri 2 desktop app whose React UI talks to this local Python
backend on `127.0.0.1:8765`, started as a sidecar (`docs/decisions.md` D9/D10).
This module is the **intent half of that backend**. It runs in-process inside
`services/atlas-core`; it is not a separate service and has no network listener
of its own.

Position in the request flow (Master PRD 8.2):

```
Tauri window -> React UI -> POST /api/v1/tasks/plan   (project owner's API)
                                      |
                            PlanningService.plan_text()        <- THIS MODULE
                              normalize -> plan -> validate
                                      |
                       ordered AtlasEvents -> SSE -> UI + 2D character
                                      |
                            validated TaskPlan + planDigest
                                      |
                 Person 1: policy -> consent -> executor -> verifier -> journal
                                      |
                            Windows UI Automation (real actions)
```

Concretely:

- **To the UI (Person 3):** the four events it renders in the transcript panel,
  plan inspector, and consent dialog. `mockMode` from `diagnostics()` drives the
  offline-vs-live badge, and `liveCallImplemented` flags let the UI avoid
  implying a working speech integration.
- **To the runtime (Person 1):** a schema-valid `TaskPlan` through the
  `Planner`/`PlanningService` interface, plus the deterministic fixture planner
  and the plan fixtures in `fixtures/` to develop against.
- **To the API (project owner):** `plan_text`, `plan_voice`,
  `verify_plan_digest`, `diagnostics`, and `mock_mode`. Wiring is requested in
  `docs/change-requests/20261029-project-owner-wire-planning-service.md`.
- **To QA (Person 4):** `fixtures/expectations.json` as a machine-readable
  contract, provider mocks, and provider-failure tests.
- **Desktop-specific behaviour honoured here:** 9Router and AWS are called from
  the backend only, so no credential is in the Tauri webview bundle; mock mode
  makes the whole app demonstrable with no credentials and no network; and the
  app's offline/live distinction is a backend fact surfaced to the UI rather
  than a UI toggle.

## 4. Commands used to build, run and test

```powershell
# Full Person 2 suite (the command that produced the results in section 5)
python -m pytest services/atlas-core/tests/intent -q

# Repository gates (project-owner owned scripts)
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check-secrets.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify-repo.ps1

# Manual end-to-end check in mock mode (no credentials, no network)
$env:PYTHONPATH="services/atlas-core/src"
python -c "from atlas.planner import PlanningService; s=PlanningService.for_mock_mode(); r=s.plan_text('task-0001','open my editor'); print(r.describe()); print([e['type'] for e in r.events])"
```

Environment: Windows, Python 3.13.5, pytest 8.3.4, jsonschema 4.23.0 present.
No package was installed and no dependency was added - the module runs on the
standard library alone, which is why `pyproject.toml` did not need to change.

**Not run, and why:** `scripts/test.ps1` and `scripts/lint.ps1` are
project-owner placeholders (`verify-repo` itself reports `scripts/test.ps1` as a
placeholder). `scripts/check-ownership.ps1 -Owner person-2` **cannot run**: it
exits with `FAIL: not a git repository`, because this workspace has no `.git`.
No Windows desktop build was produced - packaging is Person 3's `src-tauri` and
`scripts/build-desktop.ps1`, which is also still a placeholder (D11).

## 5. Tests executed and their real results

```
python -m pytest services/atlas-core/tests/intent -q
413 passed in 16.88s
```

| Test file | Result |
|---|---|
| `test_settings_and_registry.py` | 65 passed |
| `test_nine_router.py` | 44 passed |
| `test_validation.py` | 43 passed |
| `test_published_fixtures.py` | 36 passed |
| `test_events_and_digest.py` | 34 passed |
| `test_llm_planner.py` | 31 passed |
| `test_speech_providers.py` | 30 passed |
| `test_normalize.py` | 29 passed |
| `test_jsonschema_lite.py` | 28 passed |
| `test_planning_service.py` | 28 passed |
| `test_redaction.py` | 25 passed |
| `test_fixture_planner.py` | 20 passed |

Zero failures, zero skips, zero xfails in this environment.

Notable real-integration coverage rather than mocked approximation:

- `test_nine_router.py` runs a **real HTTP server** on `127.0.0.1` (ephemeral
  port) and drives the actual `urllib` path: headers, body encoding, status
  handling, retry counting. It also uses a real connection-refused socket error
  against `127.0.0.1:1`. This is why that file takes ~15 s of the 17 s total.
- `test_jsonschema_lite.py::test_full_planning_works_with_jsonschema_uninstalled`
  runs the whole planning path in a **subprocess with `jsonschema` and
  `referencing` import-blocked**, proving the zero-dependency claim rather than
  asserting it.
- `test_published_fixtures.py` replays every published fixture through the real
  validator and the real response parser.
- The three canonical `packages/contracts/examples/plan*.json` files are
  asserted directly, including that each `plan.invalid-*` fails for the reason
  its filename claims.

Repository gates:

```
scripts/check-secrets.ps1   -> PASS: no likely secrets in 213 file(s).
scripts/verify-repo.ps1     -> verify-repo: PASS (218 checks).
```

Both of these **failed before this work** (`verify-repo: FAIL (8 failed, 209
passed)`) - 7 failures from the missing canonical workstream folder and 1 from
the secret scan. Tests that need credential-shaped strings now assemble them at
runtime (`conftest.fake_credential`, `fake_aws_key_id`,
`fake_private_key_block`, `make_settings`, `env_with_credential`) so the
scanner stays fully strict instead of being given an ignore list that would
blind it to a future real leak.

## 6. Configuration or credentials still required

Nothing is required to run, test, or demo this module: with an empty
environment it is offline and deterministic. For a **live** LLM path the
project owner must supply:

| Key | Needed for | Status |
|---|---|---|
| `NINE_ROUTER_BASE_URL` | Live planning | Empty in `.env.example` |
| `NINE_ROUTER_MODEL` | Live planning | Empty in `.env.example` |
| `NINE_ROUTER_API_KEY` | Live planning | Empty in `.env.example` |
| `ATLAS_MOCK_MODE=false` | Live planning | Currently `true` |

All three `NINE_ROUTER_*` values must be present **and** mock mode off; any
other combination falls back to the fixture planner rather than producing a
half-live client.

Optional, with working defaults, requested for `.env.example` in a change
request: `NINE_ROUTER_CHAT_PATH`, `NINE_ROUTER_TIMEOUT_MS`,
`NINE_ROUTER_MAX_ATTEMPTS`, `NINE_ROUTER_MAX_OUTPUT_TOKENS`.

Open decisions that gate the live path: `docs/decisions.md` M-18 Q2 (is 9Router
an approved exception to AWS-only cloud infrastructure?), Q5 (is Transcribe
enabled in the region?), Q6 (key/model availability and limits), Q7 (allowlisted
apps and directories - `ATLAS_ALLOWED_APPS` is empty, which is Person 1's gate
rather than mine), Q10 (spend stop-work threshold). All still OPEN.

## 7. Features that are mocked, incomplete, or unverified

**Live 9Router call: UNVERIFIED.** The client is implemented against the
documented OpenAI-compatible `/v1/chat/completions` contract and is verified
against a local stub HTTP server. **No request has been made to a real 9Router
deployment**, because no endpoint or key exists in this repository and M-18
Q2/Q6 are open. The chat path is therefore configurable. Residual risk: a
deployment that differs from the documented shape would need the response
parser revisited - the error path is already typed, so such a mismatch would
surface as `MALFORMED_RESPONSE`, not as a bogus success.

**Live Amazon Transcribe: NOT IMPLEMENTED.** `AwsTranscribeProvider` is a gate,
not a stub that pretends: it validates input, returns typed
`DISABLED`/`NOT_CONFIGURED` errors, reports `live_call_implemented = False`, and
contains no code path that contacts AWS (a test asserts `boto3` is never
imported). Reasons: `INSTRUCTIONS.md` section 6 forbids a live AWS call before
Person 4 confirms region/access/budget; M-18 Q5 and Q10 are open; `boto3` is
not a declared dependency and `pyproject.toml` is project-owner owned. Voice
input in this build therefore works **only** through `MockTranscribeProvider`,
and text input is always available.

**Live Amazon Polly: NOT IMPLEMENTED.** Same gate, same reasons.
`MockPollyProvider` returns a payload prefixed `ATLAS-MOCK-AUDIO:` - not valid
audio, deliberately, so anything trying to play it fails loudly instead of
appearing to work.

**Event-schema validation under the zero-dependency fallback: NOT PERFORMED.**
`events.schema.json` uses `allOf`/`if`/`then`, which `jsonschema_lite` cannot
enforce. In that mode `validate_event` returns `[]` and `validator_info()`
reports `events_supported = False`, so `[]` means "not checked" rather than
"valid". With `jsonschema` installed - as in this environment - events are
fully validated. Events are also built by typed constructors, so the schema
check is a second line of defence rather than the only one.

**Pattern-based secret redaction is best effort.** It cannot detect an
arbitrary high-entropy string carrying no recognisable marker. The reliable
layer is exact-value redaction of credentials the process holds; that is why
settings registration is automatic and centralised.

**Keyword matching in the fixture planner is shallow by design.** It recognises
open-verb plus known-application phrasings and asks for clarification
otherwise. It is not an NLU implementation and is not meant to be - the live
planner is.

**Specialist PRD never received.** `docs/prd/03-person-2-voice-9router-prd.md`
is still the owner's placeholder, so scope was taken from the shared PRD
sections listed in `INSTRUCTIONS.md` section 3. If the specialist PRD adds
requirements, they are not covered here.

**No `git` history, branch, or PR.** This workspace has no `.git` directory, so
the required `person-2/<topic>` branch, the 30-45 minute commits, the ownership
pre-push check, and the PR into `integration` could not be performed.

## 8. Integration dependencies and blockers

**Blockers for the vertical slice (all outside Person 2's paths):**

1. **The orchestrator and API are placeholders** (project owner). Nothing calls
   `PlanningService`, so `POST /api/v1/tasks/plan` does not exist end to end
   and the UI cannot reach the planner. Change request filed. *This is the one
   blocker that keeps the demo from working.*
2. **`main.py` and `config.py` are placeholders** (project owner), so the
   backend does not start and does not bind `127.0.0.1:8765`.
3. **Person 1's policy engine, executor, verifier, and journal are
   placeholders**, so a validated plan has nothing to execute it or verify it.
   My module is complete on its side of that interface.
4. **Person 3's Tauri shell and UI components are placeholders**, so there is no
   window to render the events in.
5. **No git repository**, so nothing can be merged to `integration`.

**Non-blocking dependencies I am not waiting on:** live 9Router credentials,
AWS region/access/budget confirmation from Person 4, and the specialist PRD. All
three have a working offline path, as `INSTRUCTIONS.md` section 6 requires.

**Interface risks to flag:**

- `CONFIRMATION_REQUIRED_FROM_TIER` is set to `T1`, which is stricter than a
  literal Master PRD 7 reading (T2) and matches the canonical valid example.
  If the project owner intends T2, that is a one-constant change and a
  deliberate relaxation.
- The registry contains exactly one action (`app.open`), so the demo workflow is
  necessarily "open one allowlisted editor" (M-18 Q4, still OPEN). Additional
  actions need no planner change, only a registry PR from Person 1.
- My working folder sits at `reference/person-2-voice-9router/` while the
  ownership map says `workstreams/person-2-voice-9router/`. Pointer files make
  `verify-repo` pass; the split still needs an owner decision.

## 9. Next highest-priority action

**Wire `PlanningService` into the orchestrator and API** -
`docs/change-requests/20261029-project-owner-wire-planning-service.md`. It is
roughly a dozen lines in project-owner-owned files and it is the single step
that turns four independently-tested modules into a demonstrable path: UI ->
`POST /api/v1/tasks/plan` -> planner -> validated plan + SSE events -> consent
dialog. Until it lands, this module is complete and verified but unreachable
from the desktop application.

Second priority, which can proceed in parallel: have Person 1 consume the
`Planner` interface and the fixtures in `fixtures/plans/` so the
plan-to-policy-to-execution path is exercised against a real plan rather than a
hand-written one.
