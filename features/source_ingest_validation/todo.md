# Source ingest and validation - todo

## Contract

- [x] Confirm YouTube URL as the MVP input.
- [x] Defer local-file upload/import until URL ingestion is reliable.
- [x] Confirm 5-20 minute duration and 720p minimum.
- [x] Confirm normalized YouTube ID plus SHA-256 duplicate strategy.
- [x] Confirm failed sources can resume or start a new campaign.

## Data and service work

- [ ] Add source submission, content hash, validation result, and processing-attempt fields/models.
- [ ] Add typed validation failures and user-facing recovery messages.
- [ ] Normalize supported YouTube URL shapes to one video ID.
- [ ] Run `ffprobe` and SHA-256 before transcription.
- [ ] Enforce rights, duration, readability, and resolution gates.
- [ ] Add duplicate-active, resume-existing, and force-new-campaign domain actions.

## Workflow and verification

- [ ] Add `Normalize input` and `Validate source` stages before transcription.
- [ ] Retry temporary download failures without retrying invalid input.
- [ ] Test 4:59, 5:00, 20:00, and 20:01 duration boundaries.
- [ ] Test corrupt, below-720p, duplicate-active, failed-resume, and intentional-reprocess cases.
- [ ] Record n8n execution IDs and test evidence.

## Deferred

- [ ] Design local-file upload/import only after the YouTube URL path passes the
  pipeline-correctness milestone.
