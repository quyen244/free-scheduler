# Source ingest and validation - todo

## Contract

- [x] Confirm YouTube URL as the MVP input.
- [x] Defer local-file upload/import until URL ingestion is reliable.
- [x] Confirm 5-20 minute duration and 720p minimum.
- [x] Confirm normalized YouTube ID plus SHA-256 duplicate strategy.
- [x] Confirm failed sources can resume or start a new campaign.

## Data and service work

- [-] Add source submission, content hash, validation result, and
  processing-attempt fields/models. Hash/validation fields and job attempts are
  present; durable submission/campaign actions remain.
- [x] Add typed validation failures and user-facing recovery messages.
- [x] Normalize supported YouTube URL shapes to one video ID. Evidence:
  `test_supported_youtube_urls_normalize_to_one_video_id` and
  `test_malformed_or_lookalike_youtube_urls_are_rejected`.
- [x] Run `ffprobe` and SHA-256 before transcription.
- [x] Enforce duration, readability, and resolution gates.
- [ ] Add duplicate-active, resume-existing, and force-new-campaign domain actions.

## Workflow and verification

- [x] Accept a URL-only webhook payload and validate it inside media-service
  before transcription. Canonical JSON is updated but not imported into the
  live database.
- [ ] Retry temporary download failures without retrying invalid input.
- [x] Test 4:59, 5:00, 20:00, and 20:01 duration boundaries.
- [-] Test corrupt, below-720p, duplicate-active, failed-resume, and
  intentional-reprocess cases. Corrupt and low-resolution cases pass; campaign
  duplicate/resume actions remain.
- [ ] Record n8n execution IDs and test evidence.

## Deferred

- [ ] Design local-file upload/import only after the YouTube URL path passes the
  pipeline-correctness milestone.

Evidence: [Step 2 progress report](../../reports/pipeline-step-2-progress.md)
