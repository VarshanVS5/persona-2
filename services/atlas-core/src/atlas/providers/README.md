OWNER: Person 2 - Voice, Intent & 9Router - do not edit without owner approval

# atlas.providers

9Router client, optional AWS Transcribe/Polly adapters. Backend only; never expose keys to the UI.

Master PRD 10.4: every adapter exposes bounded requests, timeouts, typed
success/error results, and redacted logging, and a provider failure never
bypasses policy validation or turns a failed task into a success.

## Modules

| Module | Role | Status |
|---|---|---|
| `base.py` | `ProviderResult` / `ProviderError` / `ProviderErrorCode`, provider protocols. Errors redact their own message at construction. | Implemented |
| `settings.py` | Reads the `.env` provider keys. Fail-safe: mock mode on, cloud off, values clamped. Registers credentials for redaction. | Implemented |
| `redaction.py` | Two-layer secret redaction: exact known values + credential-shape patterns. | Implemented |
| `nine_router.py` | OpenAI-compatible `/v1/chat/completions` client (stdlib `urllib`), bounded timeout, bounded retries with capped jittered backoff, request/response byte ceilings. Plus `MockNineRouterClient`. | Implemented; **live gateway unverified** |
| `aws_transcribe.py` | Speech-to-text interface + `MockTranscribeProvider`. | Interface + mock implemented; **live AWS call NOT implemented** |
| `aws_polly.py` | Text-to-speech interface + `MockPollyProvider`. | Interface + mock implemented; **live AWS call NOT implemented** |

## Honest status

**9Router:** implemented against the documented OpenAI-compatible surface and
verified against a local stub HTTP server. No call has been made to a real
9Router deployment - `docs/decisions.md` M-18 Q2 and Q6 are OPEN and no endpoint
or key exists in this repository.

**AWS Transcribe / Polly:** `AwsTranscribeProvider` and `AwsPollyProvider` are
**gates, not stubs that pretend to work**. They validate input, then return a
typed `DISABLED`/`NOT_CONFIGURED` error, and they contain no code path that
contacts AWS (a test asserts `boto3` is never imported). Each reports
`live_call_implemented = False`, which `PlanningService.diagnostics()` surfaces
so the UI cannot imply a working integration. Reasons: `INSTRUCTIONS.md`
section 6 forbids a live AWS call before Person 4 confirms region/access/budget;
M-18 Q5 and Q10 are OPEN; `boto3` is not a declared dependency and
`pyproject.toml` is project-owner owned.

## Rules

- No module here is imported by `apps/desktop-ui/`. A test in
  `tests/intent/test_nine_router.py` fails if any frontend file mentions
  `NINE_ROUTER_API_KEY` or `chat/completions`.
- Credentials are read in exactly one place (`settings.py`), which is also
  where they are registered for redaction - so no log site can forget.
- `build_chat_provider`, `build_speech_to_text`, and `build_text_to_speech` are
  the single decision points for live-vs-mock, so the rule cannot drift
  between call sites.

## Tests

```powershell
python -m pytest services/atlas-core/tests/intent -q
```
