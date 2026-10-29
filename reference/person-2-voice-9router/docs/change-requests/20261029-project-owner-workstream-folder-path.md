OWNER: Person 2 - Voice, Intent & 9Router (change request addressed to the project owner)

# Change request: reconcile `reference/person-2-voice-9router/` with `OWNERSHIP.json`

- **Date:** 2026-10-29
- **Requested by:** Person 2 - Voice, Intent & 9Router
- **Target owner:** Project owner
- **Target paths:** `OWNERSHIP.json`, `.github/CODEOWNERS` (and possibly the folder location itself)
- **Severity:** low for code, real for ownership enforcement

## The discrepancy

My working folder exists in this workspace at:

    reference/person-2-voice-9router/

But `OWNERSHIP.json` and `docs/ANALYSIS.md` section 2 both record Person 2's
working folder as:

    workstreams/person-2-voice-9router/

`workstreams/` currently contains `person-1-runtime-safety/`,
`person-3-ui-character/`, and `person-4-aws-qa-integration/` - but no
`person-2-voice-9router/`. The three other personas sit where the ownership map
says they should; mine does not.

## Why it matters

`reference/` appears in no owner's `paths` list, so by the `default` rule in
`OWNERSHIP.json` it belongs to **project-owner**. Two consequences:

1. `scripts/check-ownership.ps1 -Owner person-2` will flag every file I add
   under `reference/` as outside my paths - a false positive that makes the
   pre-push check unusable for me.
2. `.github/CODEOWNERS` gives the project owner review authority over my own
   notes and fixtures, which is not the intent of
   `INSTRUCTIONS.md` section 1.

## Requested resolution (either is fine)

**Option A - move the folder** (restores the documented layout):
`git mv reference/person-2-voice-9router workstreams/person-2-voice-9router`.
My production code is unaffected; only the fixture path changes, and
`test_published_fixtures.py` already probes both locations.

**Option B - record the actual path**: add `reference/person-2-voice-9router/`
to `person-2.paths` in `OWNERSHIP.json` and the matching `.github/CODEOWNERS`
line. `scripts/verify-repo.ps1` checks that the two files agree, so both need
updating together.

I have **not** edited either file. I am working in
`reference/person-2-voice-9router/` because that is where the folder and its
`INSTRUCTIONS.md` actually are.

## Interim mitigation

Nothing in `services/atlas-core/src/atlas/{intent,planner,providers}/` or
`services/atlas-core/tests/intent/` depends on the working folder's location.
`test_published_fixtures.py` resolves the fixtures directory by probing both
candidates and skipping if neither exists, so the test suite passes either way.
