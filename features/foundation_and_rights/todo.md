# Foundation contract - todo

## Contract

- [x] Define the webhook input as one YouTube URL with no rights fields.
- [x] Define whole-video/chunk delivery mapping.
- [x] Define edits that increment `campaign_revision` and invalidate approval.
- [x] Select brand profiles rather than unrelated provider accounts.
- [x] Preserve original YouTube visuals; replace audio with Vietnamese voice and
  add subtitles, watermark, and quiet signature music.

## Domain work and verification

- [x] Implement source and delivery mapping as executable contract validation.
- [x] Add fixtures for one-, three-, variable-, and zero-chunk sources.
- [x] Test that invalid sources and missing required assets block review.
- [x] Test that metadata, target, and brand edits invalidate approval.

Evidence: [Step 1 acceptance report](../../reports/pipeline-acceptance-contract-step-1.md)
