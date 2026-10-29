OWNER: Person 2 - Voice, Intent & 9Router - do not edit without owner approval

# Person 2 working folder - Voice, Intent & 9Router

> **The content of this workstream lives in [`reference/person-2-voice-9router/`](../../reference/person-2-voice-9router/).**
>
> In this workspace Person 2's working folder was placed at
> `reference/person-2-voice-9router/`, while `OWNERSHIP.json`,
> `.github/CODEOWNERS`, and `docs/ANALYSIS.md` section 2 all record it at
> `workstreams/person-2-voice-9router/` (where the other three personas are).
> `scripts/verify-repo.ps1` checks for the canonical path, so this folder exists
> to satisfy that check without duplicating the notes, design docs, and fixtures.
>
> Pointer files only - no content is maintained here. Resolving the split is a
> project-owner decision; see
> [`20261029-project-owner-workstream-folder-path.md`](../../reference/person-2-voice-9router/docs/change-requests/20261029-project-owner-workstream-folder-path.md),
> which proposes either moving the folder here or recording the actual path in
> the ownership map. If the folder is moved, delete this directory.

| What you want | Where it is |
|---|---|
| Ownership rules, scope, timeline | [`reference/person-2-voice-9router/INSTRUCTIONS.md`](../../reference/person-2-voice-9router/INSTRUCTIONS.md) |
| Acceptance checklist with evidence | [`reference/person-2-voice-9router/acceptance-checklist.md`](../../reference/person-2-voice-9router/acceptance-checklist.md) |
| Completion report | [`reference/person-2-voice-9router/COMPLETION-REPORT.md`](../../reference/person-2-voice-9router/COMPLETION-REPORT.md) |
| Design notes | [`reference/person-2-voice-9router/design/intent-pipeline.md`](../../reference/person-2-voice-9router/design/intent-pipeline.md) |
| Fixtures for Person 1 and Person 4 | [`reference/person-2-voice-9router/fixtures/`](../../reference/person-2-voice-9router/fixtures/) |
| Change requests to other owners | [`reference/person-2-voice-9router/docs/change-requests/`](../../reference/person-2-voice-9router/docs/change-requests/) |

Production code (unaffected by this split):
`services/atlas-core/src/atlas/{intent,planner,providers}/`,
tests in `services/atlas-core/tests/intent/`.
