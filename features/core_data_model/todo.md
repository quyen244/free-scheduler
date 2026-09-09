# Core data model — todo

- [ ] Add `SourceVideo`, `SourceSubmission`, `ContentItem`, and `AssetVariant` models.
- [ ] Add `MetadataRevision` and platform metadata models/validated payloads.
- [ ] Add `Campaign`, `CampaignRevision`, and `ProcessingAttempt` models.
- [ ] Add `BrandProfile`, `BrandAccount`, and `BrandMediaAsset` models.
- [ ] Add `PublishTarget` and `PublishAttempt` models.
- [ ] Add `PostAction`, `ApprovalRequest`, `OutboxEvent`, and `AuditEvent` models.
- [ ] Add enums and legal state-transition helpers.
- [ ] Add deterministic unique and idempotency keys.
- [ ] Enforce duplicate-active versus resume/reprocess source behavior.
- [ ] Write an additive migration; do not drop `ScheduledPost` yet.
- [ ] Add a compatibility reader or one-time migration for existing rows.
- [ ] Test 1 + N + N fan-out and duplicate creation races.
