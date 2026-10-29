OWNER: Project owner - do not edit without owner approval

# Contracts changelog

Versioning rules are in [INSTRUCTIONS.md](INSTRUCTIONS.md#versioning).

## 1.0.0 - baseline (frozen before Hour 0)

- `task-plan.schema.json`: `TaskPlan`, `ActionStep`, `Condition`, `RiskTier` per Common PRD 4.1.
- `events.schema.json`: envelope with `schemaVersion` "1.0", 11 event types (Common PRD 4.3), 10 `TaskStatus` values (Common PRD 4.2).
- `action-registry.json`: `app.open` (T1, postcondition `window.exists`) and blocked categories from Master PRD section 7.
- `examples/`: six fixtures (see README.md).
