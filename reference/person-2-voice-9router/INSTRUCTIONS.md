# INSTRUCTIONS - Person 2 - Voice, Intent & 9Router

> **This folder and the paths below belong to Person 2 - Voice, Intent & 9Router ALONE.**
> Only Person 2 edits files here. Nobody else's changes are accepted in these paths without Person 2's explicit approval in PR review (enforced by `.github/CODEOWNERS` + branch protection).
> Person 2 must not edit files outside these paths (other workstreams, shared contracts, root config, scripts, CI) without approval from that path's owner or the project owner. See `docs/PERMISSIONS.md`.

Project ATLAS is a **Windows desktop application**. You build the intent/planning and provider part of the local Python backend (`services/atlas-core`). The backend runs on the user's own Windows machine, listens on `127.0.0.1` only, and is started with/by the desktop app (Tauri 2 sidecar, `docs/decisions.md` D10). 9Router and the optional AWS Transcribe/Polly are called **from this backend only**, never from the UI. Desktop actions themselves are performed by Person 1's runtime through Windows UI Automation (Master PRD 9.1); your plans are validated, never executed directly.

## 1. You may edit / You must not edit

**You may edit (owner: Person 2):**
- `workstreams/person-2-voice-9router/**` (this folder)
- `services/atlas-core/src/atlas/intent/`
- `services/atlas-core/src/atlas/planner/`
- `services/atlas-core/src/atlas/providers/` (`nine_router.py`, `aws_transcribe.py`, `aws_polly.py`)
- `services/atlas-core/tests/intent/`

**You must not edit (request a change instead, section 9):**
- `packages/contracts/**` and `docs/api-contract.md` (project owner). You may not invent action types or permissions; plans use only `action-registry.json`.
- Shared backend core: `services/atlas-core/pyproject.toml`, `README.md`, `src/atlas/__init__.py`, `main.py`, `config.py`, `api/`, `models/`, `orchestrator/`, `audit/`, `tests/conftest.py` (project owner). Dependency additions and wiring into the orchestrator/API are requested by PR.
- Root files including `.env.example`, `scripts/`, `.github/`, `OWNERSHIP.json`, `docs/` (project owner; `docs/test-results/` and `docs/setup-windows.md` belong to Person 4).
- `services/atlas-core/src/atlas/policy/`, `executor/`, `adapters/`, `verifier/`, `journal/`, `tests/runtime/`, `workstreams/person-1-runtime-safety/` (Person 1).
- `apps/desktop-ui/**`, `workstreams/person-3-ui-character/` (Person 3).
- `services/atlas-core/tests/integration/`, `workstreams/person-4-aws-qa-integration/` (Person 4).
- Never reformat, rename, or change line endings of files you do not own.

## 2. Branch, commits, merging

- Branch: `person-2/<topic>` (e.g. `person-2/planner`). The PRD name `feature/person-2-voice` is also accepted. One workstream per branch.
- Commit a working increment every 30-45 minutes. Messages: `feat(person-2): ...`, `fix(person-2): ...`, `test(person-2): ...`, `docs(person-2): ...`.
- Before every push: `git fetch` + `git rebase origin/integration`, then run
  `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check-ownership.ps1 -Owner person-2` and
  `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify-repo.ps1`.
- Open a PR into `integration`. No direct pushes to `main` or `integration`.

## 3. PRDs to read

Your person-specific PRD goes in [`prd/`](prd/README.md) (canonical name `docs/prd/03-person-2-voice-9router-prd.md`, placeholder until provided). From the shared PRDs:

- Master section 6 "Product Scope and Functional Requirements": "FR-01 — Voice and text input", "FR-02 — Intent compilation", "FR-04 — Target resolution and clarification", "FR-13 — Cloud provider integrations", "FR-14 — Privacy disclosure"
- Master section 8 "High-Level Architecture": "8.1 Components" items 3 and 8, "8.2 Request flow", "8.3 Trust boundaries"
- Master section 9 "Technology and Cloud Constraints": "9.2 AWS-only interpretation", "9.3 Budget controls"
- Master section 10 "Shared Data Contracts": "10.4 Provider interface"
- Master section 12 "Security, Privacy, and Reliability Requirements"
- Common section 4 "Shared contracts frozen before parallel work"
- Common section 7 "Four parallel workstreams" -> "Person 2 — Voice, Intent & 9Router"
- Common section 9 "Six-hour parallel execution schedule — all four active throughout"
- Common section 10 "Common acceptance checklist" -> "Person 2 — Voice/LLM"
- Common section 11 "AWS, 9Router, and budget boundaries"

## 4. Scope (Common PRD section 7)

- **Owns:** 9Router provider adapter, structured-plan generation, JSON/schema validation, bounded retries/timeouts, and optional Transcribe/Polly integration.
- **Must produce (deliverables):** Provider configuration, request/response interface, mock fixtures, redacted logging, and provider-failure tests.
- **Must not:** Put API keys in the UI, invent action permissions, or execute model output.

## 5. Dependencies (verbatim from `docs/ANALYSIS.md` section 4)

| Consumes from | Provides to |
|---|---|
| Project owner: `task-plan.schema.json`, `action-registry.json` (allowed action types), `events.schema.json`, `.env.example` keys `NINE_ROUTER_*`, `AWS_TRANSCRIBE_ENABLED`, `AWS_POLLY_ENABLED`, `AWS_REGION`, `ATLAS_MOCK_MODE`. Person 4: AWS region/access/budget confirmation before any live Transcribe/Polly call. | Person 1 & orchestrator: schema-valid `TaskPlan` (planner interface) and a deterministic fixture planner. Person 3: events `transcript`, `plan_ready`, `clarification_required`, `provider_error`. Person 4: valid/invalid plan fixtures (in `workstreams/person-2-voice-9router/fixtures/`), provider mocks, provider-failure tests. |

These are interface dependencies, never "wait for" dependencies.

## 6. How to work without waiting

- Code against `packages/contracts/` only, never against Person 1's runtime internals.
- Live 9Router/AWS access is optional: start with a deterministic fixture planner and provider mocks (`ATLAS_MOCK_MODE=true`). Text input is always the fallback for voice.
- Validate your output against `packages/contracts/task-plan.schema.json` and `action-registry.json`; reproduce the rejections in `examples/plan.invalid-*.json`.
- No live AWS call before Person 4 confirms region/access/budget.

## 7. Timeline (`docs/ANALYSIS.md` section 7 + Common PRD section 9)

| Time | Your work |
|---|---|
| Hour 0-0:15 | Clone, read the ownership map, run the baseline in mock mode, start immediately. |
| 0:15-2:00 | Implement the planner/provider interface, 9Router client, strict schema validation, and deterministic fixture planner. Live provider access is optional. Checkpoint: independently testable slice. |
| 2:00-3:30 | Expose a stable planner interface and publish valid/invalid plan fixtures. Checkpoint: UI-to-planner-to-policy path works with mocks. |
| 3:30-4:45 | Test provider outage, malformed output, retries/timeouts, and secret redaction. Stop feature expansion at 4:45. |
| 4:45-5:00 | Feature freeze: commit a stable increment, update your checklist, report status. |
| 5:00-6:00 | Fix only your owned modules. Verify the provider can fail safely without bypassing validation. |

## 8. Definition of done

Every item in [`acceptance-checklist.md`](acceptance-checklist.md) is checked, your tests pass, `check-ownership.ps1 -Owner person-2` passes, and your work is merged to `integration`.

## 9. Requesting a change in someone else's area

1. Do not edit the file on your branch.
2. Open a GitHub issue with the "Change request" template tagging the owner, or write `workstreams/person-2-voice-9router/docs/change-requests/YYYYMMDD-<target-owner>-<topic>.md` in your own folder and link it in the issue.
3. The owner makes the change on their own branch. If they ask you to make it, your PR needs their approval and the project owner adds the `ownership-override` label.
4. Contract changes follow `packages/contracts/INSTRUCTIONS.md` (propose -> affected owners approve -> project owner merges).
5. Meanwhile keep working against the current contract and mocks.
