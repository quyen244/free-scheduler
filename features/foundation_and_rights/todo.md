# Foundation and rights - todo

## Contract

- [x] Define `rights_status` as owned, licensed, permission, public_domain, or unknown.
- [x] Define optional rights evidence note/URL fields.
- [x] Define whole-video/chunk delivery mapping.
- [x] Define edits that increment `campaign_revision` and invalidate approval.
- [x] Select brand profiles rather than unrelated provider accounts.
- [x] Preserve original YouTube visuals; replace audio with Vietnamese voice and
  add subtitles, watermark, and quiet signature music.

## Domain work and verification

- [x] Implement rights and delivery mapping as executable contract validation.
- [x] Add fixtures for one-, three-, variable-, and zero-chunk sources.
- [x] Test that unknown rights and missing required assets block review.
- [x] Test that metadata, target, and brand edits invalidate approval.

Evidence: [Step 1 acceptance report](../../reports/pipeline-acceptance-contract-step-1.md)
