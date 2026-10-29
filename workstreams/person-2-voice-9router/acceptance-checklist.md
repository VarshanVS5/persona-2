OWNER: Person 2 - Voice, Intent & 9Router - do not edit without owner approval

# Acceptance checklist - Person 2 - Voice/LLM (Common PRD section 10)

Pointer file. The checklist with its evidence is maintained at
[`reference/person-2-voice-9router/acceptance-checklist.md`](../../reference/person-2-voice-9router/acceptance-checklist.md)
and is not duplicated here. See [`README.md`](README.md) for why the path is split.

## Summary as of 2026-10-29

Six Common PRD section 10 items for Person 2: **all six met**, each backed by
tests you can run.

```powershell
python -m pytest services/atlas-core/tests/intent -q
```

413 tests, all passing.

- [x] 9Router is called from backend only
- [x] Structured output is schema-validated
- [x] Unknown actions and invalid JSON are rejected
- [x] Provider timeouts and bounded retries work
- [x] No secrets appear in logs
- [x] Mock mode and text fallback work

Explicitly **not** done, with reasons in the full checklist:

- [ ] Live 9Router call verified against the real gateway (no endpoint or key
      exists; `docs/decisions.md` M-18 Q2 and Q6 are OPEN)
- [ ] Live Amazon Transcribe / Polly (forbidden before Person 4 confirms
      region/access/budget; `boto3` is not a declared dependency)
- [ ] Merged to `integration` (this workspace is not a git repository)
