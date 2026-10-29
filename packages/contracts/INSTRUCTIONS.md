# INSTRUCTIONS - packages/contracts (shared contracts)

> **This folder is owned by the Project owner (lead / integration owner) ALONE.**
> Nobody else commits changes here without the project owner's explicit approval in PR review (enforced by `.github/CODEOWNERS` + branch protection, see `docs/BRANCH_PROTECTION.md`).

## What lives here

The cross-team interfaces taken from Common PRD section 4 "Shared contracts frozen before parallel work" and Master PRD section 10 "Shared Data Contracts":
`task-plan.schema.json`, `events.schema.json`, `action-registry.json`, `examples/`. The REST surface is in `docs/api-contract.md` (same owner, same rules).

## Golden rule: code against contracts, never against each other's internals

- Person 1, 2, 3 and 4 import or read **only** these files (and `docs/api-contract.md`) to learn another workstream's payloads.
- Never import, copy, or depend on code inside another person's module (`services/atlas-core/src/atlas/<their package>/`, `apps/desktop-ui/src/...`) to "find out" a format. If the contract does not say it, it is not agreed.
- Never create a private variant of a payload. If you need a field, request a contract change.
- Mocks and tests use `examples/` so every workstream can work from Hour 0 without waiting.

## Change-request process

1. **Propose.** Open an issue with the "Change request" template (or a PR from your own branch that only touches `packages/contracts/**`), tagging `@atlas-project-owner` and every affected owner. State: what changes, why, which producers/consumers are affected, and whether it is breaking. You may also record the proposal as a file in your own folder: `workstreams/<your-folder>/docs/change-requests/YYYYMMDD-contracts-<topic>.md`.
2. **Both affected owners approve.** The producer and every consumer of the changed contract (see the table in `README.md`) approve the PR in GitHub review. For a breaking change, all four people are notified (Common PRD section 8 rule 6).
3. **Update schema and examples together.** The same PR updates the schema/registry, the matching `examples/`, `CHANGELOG.md`, and the version numbers below. A PR that changes a schema without its examples is not merged.
4. **Verify.** `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify-repo.ps1` passes.
5. **Lead merges.** Only the project owner merges contract PRs. Use the `ownership-override` label on the PR if the proposer's branch is not `project-owner/...` (the ownership CI check otherwise blocks it).

During the sprint, Person 4 (integration/QA) runs the contract tests and reports breakage, but does not change contracts unilaterally (Common PRD section 7).

## Versioning

- Each contract carries a semantic version: `x-contract-version` in the JSON Schemas, `contractVersion` in `action-registry.json`, `version` in `package.json`. All three move together.
- **PATCH** (1.0.x): descriptions, examples, or docs only; no payload changes.
- **MINOR** (1.x.0): additive and backward-compatible, e.g. a new optional field, a new registered action, a new event type consumers may ignore.
- **MAJOR** (x.0.0): breaking, e.g. removing/renaming a field, making a field required, changing an enum value. Requires all four people's approval and a new `schemaVersion` value on events (e.g. "2.0").
- Events always carry `schemaVersion`; consumers must reject or explicitly handle versions they do not know.
- Every version bump gets a `CHANGELOG.md` entry.
- Contracts are frozen from Hour 0. During the sprint only the project owner may approve changes, and only to unblock cross-boundary work.

## What the project owner must not do

- Merge a contract change without approval from the affected owners.
- Change a contract to fit one workstream's implementation details.
