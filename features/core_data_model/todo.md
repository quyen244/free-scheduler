# Core data model — todo

- [ ] Add `SourceVideo`, `ContentItem`, and `AssetVariant` models.
- [ ] Add `Campaign`, `PublishTarget`, and `PublishAttempt` models.
- [ ] Add `PostAction`, `ApprovalRequest`, `OutboxEvent`, and `AuditEvent` models.
- [ ] Add enums and legal state-transition helpers.
- [ ] Add deterministic unique and idempotency keys.
- [ ] Write an additive migration; do not drop `ScheduledPost` yet.
- [ ] Add a compatibility reader or one-time migration for existing rows.
- [ ] Test 1 + N + N fan-out and duplicate creation races.

