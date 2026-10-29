# Acceptance checklist - Person 2 - Voice/LLM (Common PRD section 10)

Status as of 2026-10-29. Evidence is a test you can run:

```powershell
python -m pytest services/atlas-core/tests/intent -q
```

370 tests, all passing (see `../COMPLETION-REPORT.md` for the full run output).

- [x] **9Router is called from backend only.**
      Implemented in `services/atlas-core/src/atlas/providers/nine_router.py`;
      the key is read from the backend environment by
      `atlas.providers.settings`. Enforced by a structural test that fails if
      any file under `apps/desktop-ui/` mentions `NINE_ROUTER_API_KEY` or
      `chat/completions` (`test_nine_router.py::test_no_frontend_file_references_the_api_key`).

- [x] **Structured output is schema-validated.**
      `atlas.planner.validation.PlanValidator` validates against
      `packages/contracts/task-plan.schema.json` *and* against
      `action-registry.json` (blocked category, registered action,
      `argumentsSchema`, risk-tier equality, timeout ceiling, required
      condition types, step numbering, unique idempotency keys, consent not
      lowered, T3 refused). The three canonical contract examples are asserted
      directly: `plan.valid.json` passes, both `plan.invalid-*.json` are
      rejected for the reason their filename claims.
      Works with or without the `jsonschema` package - the zero-dependency
      fallback is proven end to end in a subprocess with the import blocked
      (`test_jsonschema_lite.py::test_full_planning_works_with_jsonschema_uninstalled`).

- [x] **Unknown actions and invalid JSON are rejected.**
      Unknown and blocked action types, invalid/missing arguments, non-JSON
      replies, JSON arrays, oversized replies, and every one of the seven
      blocked categories are covered in `test_validation.py` and
      `test_llm_planner.py`. A rejected outcome never carries a plan object
      and never carries a digest.

- [x] **Provider timeouts and bounded retries work.**
      Timeout, network, auth, rate-limit, upstream, malformed, oversized, and
      HTTP-200-with-error paths are all tested, including against a **real
      local HTTP server**. Retryable codes use the full attempt budget and
      stop on success; non-retryable codes are attempted exactly once; backoff
      grows and is capped. Planning adds its own ceiling of two provider calls
      per request. `test_nine_router.py`, `test_llm_planner.py`.

- [x] **No secrets appear in logs.**
      Two-layer redaction in `atlas.providers.redaction`: exact-value
      redaction of every credential this process holds (registered
      automatically when settings load) plus pattern redaction for credential
      shapes. `ProviderError` redacts its message at construction, so an error
      built from an exception string cannot leak. Event payloads are redacted
      before they are returned. Tests assert the prompt, the bearer header,
      and the key never reach a log record, an event, or
      `settings.describe()`. `test_redaction.py`.
      *Disclosed limit:* pattern redaction cannot detect an arbitrary
      high-entropy string with no recognisable marker; that is what the
      known-value layer is for.

- [x] **Mock mode and text fallback work.**
      `ATLAS_MOCK_MODE=true`, or any missing `NINE_ROUTER_*` value, selects the
      deterministic `FixturePlanner` - no network, no credentials, stable plan
      digest. Mock mode is the **default** for an empty environment, and
      "mock mode off but unconfigured" still falls back rather than producing a
      broken live client. Every mocked result is labelled (`mocked=True`,
      `is_mock`, `diagnostics()["mockMode"]`) so the UI can show offline mode.
      Voice failure emits `provider_error` **plus** a
      `clarification_required` telling the user to type - it never fails the
      task (Master PRD FR-01). `test_planning_service.py`,
      `test_fixture_planner.py`, `test_speech_providers.py`.

## Not done, and honestly so

- [ ] **Live 9Router call verified against the real gateway.**
      Not possible yet: `docs/decisions.md` M-18 Q2 (is 9Router an approved
      exception?) and Q6 (key/model availability and limits) are both OPEN, and
      no endpoint or key exists in this workspace. The client is implemented to
      the documented OpenAI-compatible `/v1/chat/completions` contract and is
      verified against a local stub server. **The live integration is
      unverified.**

- [ ] **Live Amazon Transcribe / Polly.**
      Deliberately not implemented. `INSTRUCTIONS.md` section 6 forbids a live
      AWS call before Person 4 confirms region/access/budget; M-18 Q5 and Q10
      are OPEN; `boto3` is not a declared dependency and
      `services/atlas-core/pyproject.toml` is project-owner owned.
      `AwsTranscribeProvider` and `AwsPollyProvider` are **real gates**, not
      stubs that pretend: they return typed `DISABLED`/`NOT_CONFIGURED` errors,
      report `live_call_implemented = False`, and have no code path that
      contacts AWS (asserted by a test that checks `boto3` is never imported).

- [ ] **Merged to `integration`.**
      Blocked on repository state, not on my work: this workspace has no git
      repository and no remote, so there is no `integration` branch to open a
      PR against. See `../COMPLETION-REPORT.md`.

- [ ] **`check-ownership.ps1 -Owner person-2` passes cleanly.**
      Reports false positives for my own working folder because
      `OWNERSHIP.json` records it as `workstreams/person-2-voice-9router/`
      while it actually exists at `reference/person-2-voice-9router/`.
      Change request filed: `docs/change-requests/20261029-project-owner-workstream-folder-path.md`.
      All of my *production* paths pass.
