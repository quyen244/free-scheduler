# Source ingest and validation - todo

## Contract

- [x] Confirm YouTube URL as the MVP input.
- [x] Defer local-file upload/import until URL ingestion is reliable.
- [x] Confirm 5-20 minute duration and 720p minimum.
- [x] Confirm normalized YouTube ID plus SHA-256 duplicate strategy.
- [x] Confirm failed sources can resume or start a new campaign.

## Data and service work

- [x] Persist the submitted URL, source identity/hash, validation result,
  campaign, and processing-attempt history. Evidence: `SourceVideo`, `Campaign`,
  and `ProcessingAttempt`; the user deferred a separate `SourceSubmission`
  table for the URL-only MVP on 2026-09-11.
- [x] Add typed validation failures and user-facing recovery messages.
- [x] Normalize supported YouTube URL shapes to one video ID. Evidence:
  `test_supported_youtube_urls_normalize_to_one_video_id` and
  `test_malformed_or_lookalike_youtube_urls_are_rejected`.
- [x] Run `ffprobe` and SHA-256 before transcription.
- [x] Enforce duration, readability, and resolution gates.
- [x] Add duplicate-active, resume-existing, and force-new-campaign domain
  actions. Evidence: 35 passing campaign tests, including database-level race
  and idempotency cases.

## Workflow and verification

- [x] Accept a URL-only webhook payload and validate it inside media-service
  before transcription. The canonical 36-node workflow was imported in place
  and re-exported with its identity and credentials preserved; it remains
  inactive.
- [x] Retry temporary download/audio-extraction failures without retrying
  invalid input or source-policy failures. Evidence: media-service Docker suite,
  31 passed on 2026-09-11.
- [x] Test 4:59, 5:00, 20:00, and 20:01 duration boundaries.
- [x] Test corrupt, below-720p, duplicate-active, failed-resume, and
  intentional-reprocess cases. Evidence: media-service and campaign suites.

## Deferred

- [ ] Design local-file upload/import only after the YouTube URL path passes the
  pipeline-correctness milestone.
- [ ] Record a live external n8n execution ID during the signed handoff
  milestone. The user explicitly deferred this from Step 2 on 2026-09-11.

Evidence: [Step 2 progress report](../../reports/pipeline-step-2-progress.md)
