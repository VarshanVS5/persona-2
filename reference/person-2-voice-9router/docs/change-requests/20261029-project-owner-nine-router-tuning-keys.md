OWNER: Person 2 - Voice, Intent & 9Router (change request addressed to the project owner)

# Change request: four optional `NINE_ROUTER_*` tuning keys in `.env.example`

- **Date:** 2026-10-29
- **Requested by:** Person 2 - Voice, Intent & 9Router
- **Target owner:** Project owner
- **Target paths:** `.env.example`
- **Procedure:** `INSTRUCTIONS.md` section 9 - I have not edited `.env.example`.

## Why

`.env.example` defines `NINE_ROUTER_BASE_URL`, `NINE_ROUTER_MODEL`, and
`NINE_ROUTER_API_KEY`. The client also needs to know *which path* to call and
how hard to try. Those are currently compile-time constants with safe
defaults, which is fine for the sprint but makes two real situations awkward:

1. `docs/decisions.md` M-18 Q6 ("Is the 9Router key/model available, and what
   are its usage limits?") is still OPEN. Until the deployment is confirmed,
   the chat path may not be `/v1/chat/completions`.
2. Tuning the timeout or the retry ceiling for a slow network currently
   requires a code change.

## Requested addition

```dotenv
# Optional 9Router tuning. All four have working defaults; leave blank to use them.
NINE_ROUTER_CHAT_PATH=/v1/chat/completions
NINE_ROUTER_TIMEOUT_MS=20000
NINE_ROUTER_MAX_ATTEMPTS=3
NINE_ROUTER_MAX_OUTPUT_TOKENS=1200
```

## Current behaviour without this change

`atlas.providers.settings.ProviderSettings` already reads these keys and falls
back to exactly the values above, clamping each to a sane band
(timeout 1,000-120,000 ms; attempts 1-5; output tokens 256-8,192) and ignoring
a malformed value rather than crashing. **Nothing is broken today** - this
request is about making the knobs discoverable and documented rather than
implied by code.

`/v1/chat/completions` is the documented 9Router surface: 9Router is described
publicly as an OpenAI-compatible proxy exposing `/v1/chat/completions`
([9Router setup guide](https://github.com/didin-lab/9router-setup-guide),
[9Router architecture notes](https://github.com/decolua/9router/blob/master/docs/ARCHITECTURE.md)).
I have implemented to that documented contract and verified it against a local
stub HTTP server. **No live 9Router call has been made or verified.**
Content was rephrased for compliance with licensing restrictions.

## Related open decisions

Please also resolve, since they gate the live path:

- M-18 Q2: is 9Router an approved external LLM exception to AWS-only cloud infrastructure? (OPEN)
- M-18 Q6: key/model availability and usage limits. (OPEN)
- M-18 Q10: the spend stop-work threshold. (OPEN)
