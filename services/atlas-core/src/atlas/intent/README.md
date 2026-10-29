OWNER: Person 2 - Voice, Intent & 9Router - do not edit without owner approval

# atlas.intent

Intent compilation and target resolution / clarification (FR-02, FR-04).

Every user request - typed or spoken - enters the backend through this package
before anything else happens.

## Modules

| Module | Role |
|---|---|
| `normalize.py` | `normalize_request()` - bound length, strip control/zero-width/bidirectional characters, NFKC + whitespace fold, redact secrets, flag injection markers, and return `needs_clarification` instead of guessing. |
| `voice.py` | `VoiceIntake` - wraps a `SpeechToTextProvider` with normalization and a guaranteed text fallback. Never raises, never fails a task. |

Intent *compilation* (turning the normalized request into a structured plan)
lives in `atlas.planner`; this package produces the trusted, cleaned input it
consumes.

## Two properties worth knowing

**Secrets never leave here.** A pasted credential is redacted in
`normalize_request`, so it cannot reach the 9Router prompt, the task plan, an
event payload, or Person 1's journal (Master PRD 8.3, FR-14). The redacted text
is what becomes `plan.userRequest`.

**Injection markers are flagged, not enforced.** Phrases like "ignore previous
instructions" are recorded on the result for logging and audit, and the request
is still processed. The defence is structural: the request reaches the model as
a JSON string value, and whatever the model returns is schema- and
registry-validated by `atlas.planner.validation` before it can become an
action. A marker list is bypassable; the validator is not.

## Tests

```powershell
python -m pytest services/atlas-core/tests/intent -q
```
