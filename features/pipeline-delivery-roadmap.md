# Pipeline delivery roadmap

Source design: [automated-reup-platform-system-design.md](../reports/automated-reup-platform-system-design.md)

Status legend: `[ ]` not started, `[-]` in progress, `[x]` verified, `[!]` blocked.

For every completed step, replace `Pending` under `Evidence` with a test name,
execution ID, external sandbox ID, screenshot, or report path.

## Current focus

Complete Steps 0-3 before implementing real account credentials, Telegram
approval, or a platform publisher.

Immediate objective:

> Make one accepted source reliably produce selected whole/per-chunk metadata,
> one valid 1920x1080 whole video, every required 1080x1920 chunk, and
> mock-branded variants described by a validated manifest.

## Known baseline

Verified on September 9, 2026:

- The live n8n database workflow `reupPipeline` has 30 nodes.
- `automation/workflows/f7-render.json` has 24 nodes and is older than the live
  database workflow.
- `data/3gi_15UH9fQ/processed/final.mp4` is 1080x1920, so it is not the required
  1920x1080 YouTube output.
- `data/3gi_15UH9fQ/chunks/001/final.mp4` fails `ffprobe`, although its database
  row says `rendered`.

## Pending implementation decisions

- [x] Use Vietnamese metadata with curiosity-driven, engaging,
  never-misleading copy and at most two relevant emojis per platform object.
- [x] Use a cost-first fixture evaluation to select the OpenAI model.
- [x] Use `gpt-5.6-luna` with `reasoning.effort: none`; warn at an estimated
  `$0.02` per campaign without stopping work midway. The representative
  whole-video plus three-chunk fixture cost `$0.003704`.
- [x] Calibrate signature-music loudness, speech ducking, looping, fade, and
  missing-file behavior against a fixture. Evidence:
  `test_signature_music_ducks_loops_and_fades_without_lowering_speech` and
  `test_missing_signature_music_fails_during_brand_preflight`.
- [ ] Confirm Shopee Affiliate Open API `app_id` and `secret_key` availability.

## Step 0 - freeze the live workflow

Goal: create a safe, version-controlled baseline before changing n8n nodes.

- [x] Export live workflow ID `reupPipeline` from the n8n database.
- [x] Save it as `automation/workflows/reup-pipeline.json`.
- [x] Confirm the export contains 30 nodes and the six database-only validation
  and Telegram nodes.
- [x] Compare the export with `f7-render.json` and record meaningful differences.
- [x] Label `f3` through `f7` workflow files as historical phase snapshots.
- [x] Update automation documentation to use the canonical workflow filename.
- [x] Validate `docker compose config` and confirm all five services are healthy.

Evidence:

- [Step 0 baseline evidence](../reports/n8n-workflow-baseline-step-0.md)

Done when: the live database workflow can be recreated from the reviewed
canonical JSON without losing nodes, connections, or credential references.

## Step 1 - lock the acceptance contract

Goal: turn the product idea into assertions that later stages must satisfy.

- [x] Complete the [Foundation contract](foundation_and_rights/todo.md).
- [x] Complete the executable [Pipeline acceptance contract](pipeline_acceptance_contract/todo.md).
- [x] Document URL-only YouTube input, validation, and reusable source identity.
- [x] Defer local-file input until after the URL pipeline is reliable.
- [x] Document variable, unique, sentence-safe 4-5 minute chunks.
- [x] Document whole-video and per-chunk metadata outputs and edit invalidation.
- [x] Document Vietnamese language, curiosity-driven truthful tone, two-emoji
  maximum, and cost-first model-selection policy.
- [x] Document brand-profile account grouping, watermark, and signature music.
- [x] Document failed-stage resume, target-only retry, notifications, and retention.
- [x] Classify model/music calibration as Step 2 work and Shopee credentials as
  later commerce work; none changes the Step 1 behavioral contract.
- [x] Define reference fixtures for 5-, 9-, 10-, 13:42-, and 20-minute sources.
- [x] Require one whole edited 1920x1080 YouTube asset per selected brand.
- [x] Require N edited 1080x1920 chunk assets per selected brand, shared by that
  brand's Facebook and TikTok targets.
- [x] Require exactly seven targets for a three-chunk, one-brand fixture:
  `1 YouTube + 3 Facebook + 3 TikTok`.
- [x] Assert that no upload target can run before approval.
- [x] Assert that one approval covers the complete campaign revision.
- [x] Assert that an edit after approval invalidates the approval.
- [x] Assert that TikTok success means `draft delivered`, not `publicly posted`.

Evidence:

- [Lifecycle architecture](../reports/reup-pipeline-lifecycle.md)
- [Interactive lifecycle prototype](../reports/reup-pipeline-lifecycle-prototype.html)
- [Step 1 executable-contract evidence](../reports/pipeline-acceptance-contract-step-1.md)
- `python -m unittest discover -s automation/acceptance-contract -p "test_*.py" -v`
  - 12 tests passed on September 9, 2026.

Done when: confirmed specs and fixture/domain tests express source validation,
metadata, ratios, brands, revisions, recovery, and expected delivery behavior
without calling an external platform.

## Step 2 - produce the correct media variants

Goal: make the local production path correct from input validation through
metadata, branded rendering, and media output.

Status: in progress. Source preflight, balanced chunk planning, metadata
generation, the canonical n8n metadata/media gates, and the revisioned
clean/branded media job are verified; the full-length fixture matrix and live
n8n import remain.

- [ ] Complete [Source ingest and validation](source_ingest_validation/todo.md).
- [ ] Complete [Chunk and whole-video metadata generation](chunk_metadata_generation/todo.md).
- [x] Complete the mock-profile work in [Brand profiles](brand_profiles/todo.md).
  Durable app models remain P1 work. Evidence: calibrated brand fixtures and
  production endpoint job `ab1397127dec471a8a1365480b86c069`.
- [ ] Complete [Media variants and manifest](media_variants_manifest/todo.md).
- [x] Insert metadata generation after `Chunked` and before voice/render in the
  reviewed canonical JSON. Live import remains intentionally pending.
- [x] Define and Docker-test strict `metadata.v1` schemas for one YouTube result
  and each chunk's visual, Facebook, and TikTok metadata.
- [x] Replace greedy four-minute chunking with the accepted balanced policy.
- [x] Persist stable `part_<n>` names and transcript-boundary shift evidence.
- [x] Add typed source validation with duration, readability,
  resolution, `ffprobe`, and SHA-256 checks before transcription.
- [x] Produce the revisioned `whole-16x9.mp4` as a real landscape render.
  Evidence: `/media-revision/jobs` fixture job
  `f4f842d3e00c4288a3c5fa464351ed94` wrote
  `/data/jNQXAC9IVRw/outputs/clean/revision/3/whole-16x9.mp4`.
- [x] Never use a concatenation of vertical chunks as the YouTube asset. The
  renderer reads `raw.mp4` directly, and the canonical n8n render node calls
  `/media-revision/jobs`. Evidence:
  [n8n media-revision integration](../reports/n8n-media-revision-integration-step-2.md).
- [x] Produce `N` clean vertical chunk masters. Evidence:
  `test_complete_revision_creates_every_part_and_reuses_same_brand_verticals`
  covers two parts; the 19-second fixture wrote
  `/data/jNQXAC9IVRw/outputs/clean/revision/3/vertical/part_1-9x16.mp4`.
- [x] Derive branded 16:9 and 9:16 variants with mock watermark/signature music.
  Evidence: `/media-revision/jobs` fixture job
  `f4f842d3e00c4288a3c5fa464351ed94` wrote both mock-brand variants with
  H.264/AAC probe evidence and zero failures.
- [x] Let same-brand Facebook and TikTok reference the same physical vertical
  asset. Evidence:
  `test_complete_revision_creates_every_part_and_reuses_same_brand_verticals`
  asserts branded vertical paths are brand-level paths, not platform-specific
  Facebook/TikTok paths.
- [x] Preserve stable asset IDs and paths across safe retries. Evidence:
  same-revision retry job `cd9b3b170c204875afcfc59ece35c068` completed with
  the existing ready manifest and four assets.
- [ ] Test 5-, 9-, 10-, 13:42-, and 20-minute fixtures inside Docker.
- [x] Test OpenAI transient failure and one corrupt-render recovery without
  repeating successful stages. Evidence:
  `test_selective_retry_persists_provenance_and_reuses_selected_results` and
  `test_corrupt_brand_asset_retry_reuses_verified_peers`.
- [x] Inspect representative frames from both layouts. Evidence:
  `reports/step2-revision3-branded-whole-frame.jpg` and
  `reports/step2-revision3-branded-part1-frame.jpg`.

Expected output:

```text
data/<video_id>/
|-- outputs/
|   |-- clean/
|   |   `-- revision/<render_revision>/
|   |       |-- whole-16x9.mp4
|   |       `-- vertical/part_<n>-9x16.mp4
|   `-- brands/<brand_id>/revision/<n>/
|       |-- whole-16x9.mp4
|       `-- vertical/part_<n>-9x16.mp4
|-- metadata/
|   |-- youtube.json
|   `-- chunks/part_<n>.json
`-- media-manifest.json
```

Evidence:

- [Step 2 progress](../reports/pipeline-step-2-progress.md)
- [Metadata model fixture](../reports/metadata-model-fixture-2026-09-10.md)
- [n8n metadata integration](../reports/n8n-metadata-integration-step-2.md)
- [n8n media-revision integration](../reports/n8n-media-revision-integration-step-2.md)
- [Media manifest contract](../reports/media-manifest-contract-step-2.md)
- Render-service media revision Docker subset:
  `docker compose run --rm -e PYTHONPATH=/app -w /app render-service python -m pytest -q tests/test_manifest.py tests/test_clean_landscape.py tests/test_clean_vertical_and_brand.py`
  - 20 tests passed on September 10, 2026.
- Render-service model-free contract suite:
  `docker compose run --rm -e PYTHONPATH=/app -w /app render-service python -m pytest -q -m no_pipeline`
  - 48 tests passed, 59 deselected on September 10, 2026.
- Short fixture production endpoint:
  `/media-revision/jobs` for `jNQXAC9IVRw`, render revision 3, job
  `f4f842d3e00c4288a3c5fa464351ed94`: ready manifest, 4 assets, 0 failures.
- Same-revision retry:
  job `cd9b3b170c204875afcfc59ece35c068`: reused existing ready manifest,
  4 assets, 0 failures.
- Frame evidence:
  `reports/step2-revision3-branded-whole-frame.jpg` and
  `reports/step2-revision3-branded-part1-frame.jpg`.

Done when: a three-chunk source creates valid selected metadata, clean masters,
and mock-branded landscape/vertical variants without ambiguous filenames or
unrecoverable stage failure.

## Step 3 - make the manifest the completion gate

Goal: prevent missing or corrupt media from entering review and publishing.

- [x] Define and version the media manifest JSON schema. Evidence: committed
  `media-manifest.v1` schema and 12 Docker contract tests.
- [ ] Run `ffprobe` on every required asset before render completion.
- [ ] Verify that each file exists and can be opened.
- [ ] Verify expected width, height, codec, duration, audio stream, video stream,
  and non-zero byte size.
- [ ] Calculate and record SHA-256 for every asset.
- [ ] Record warnings and validation failures in the manifest.
- [ ] Reproduce and fix the current corrupt `chunks/001/final.mp4` case.
- [ ] Ensure a corrupt file cannot remain in the `rendered` state.
- [ ] Set `ready_for_review` only after the complete manifest passes validation.

Evidence:

- Pending.

Done when: removing, truncating, corrupting, or changing the ratio of any
required output makes the pipeline fail before app handoff.

## Step 4 - build the handoff and simulate target fan-out

Goal: prove the delivery plan without calling social APIs.

- [ ] Complete [Core data model](core_data_model/todo.md).
- [ ] Complete [n8n-to-app handoff](n8n_app_handoff/todo.md).
- [ ] Ingest the three-chunk manifest idempotently.
- [ ] Generate one YouTube whole-video target.
- [ ] Generate three Facebook chunk targets.
- [ ] Generate three TikTok draft targets.
- [ ] Show the exact target count before approval.
- [ ] Reject a YouTube target that references a chunk.
- [ ] Reject a Facebook or TikTok target that references the whole asset.
- [ ] Repeat the handoff and confirm no duplicate record is created.

Evidence:

- Pending.

Done when: the app produces the correct seven-target plan from the reference
manifest with no external API calls.

## Step 5 - add review and one Telegram approval

Goal: inspect one immutable campaign revision and approve its full fan-out once.

- [ ] Complete [Social account vault](social_account_vault/todo.md).
- [ ] Complete [Review inbox](review_inbox/todo.md).
- [ ] Complete [Telegram campaign approval](telegram_campaign_approval/todo.md).
- [ ] Validate one account per platform before adding account groups.
- [ ] Show asset previews, warnings, selected accounts, and seven targets.
- [ ] Show generated YouTube and per-chunk metadata with edit/regenerate controls.
- [ ] Add `Save changes -> prepare new revision -> send approval again`.
- [ ] Add `Retry failed stage` without restarting successful media work.
- [ ] Send one message with `Approve all`, `Reject`, and `Open review`.
- [ ] Consume a valid approval exactly once.
- [ ] Reject duplicate, expired, wrong-chat, wrong-operator, and old-revision
  callbacks.

Evidence:

- Pending.

Done when: one valid Telegram action approves all seven targets exactly once.

## Step 6 - prove the queue with fake publishers

Goal: prove retries and idempotency before spending platform quota.

- [ ] Complete the core of [Publisher queue and worker](publisher_queue_worker/todo.md).
- [ ] Implement fake YouTube, Facebook, and TikTok provider adapters.
- [ ] Confirm approval creates exactly seven independently claimable targets.
- [ ] Confirm duplicate approval delivery does not create fourteen targets.
- [ ] Kill and restart the worker during a claimed target.
- [ ] Confirm a stale lease is recovered without duplicate delivery.
- [ ] Make one fake target fail while the other six succeed.
- [ ] Retry only the failed target.
- [ ] Show `partial_failure` until all required targets reach their accepted outcomes.
- [ ] Preserve attempt history and idempotency keys.

Evidence:

- Pending.

Done when: restart, duplicate messages, and partial failure cannot lose or
duplicate targets in the simulated campaign.

## Step 7 - publish one private YouTube video

Goal: validate the first real provider with the whole 16:9 asset.

- [ ] Complete the YouTube items in [Platform sandbox](platform_sandbox/todo.md).
- [ ] Complete [YouTube whole-video publishing](youtube_whole_video/todo.md).
- [ ] Upload only the whole 1920x1080 asset.
- [ ] Use private visibility for the first acceptance run.
- [ ] Upload and verify the thumbnail.
- [ ] Persist the resumable session and final video ID.
- [ ] Reconcile processing state without creating another video.

Evidence:

- Pending.

Done when: one approved campaign creates one private YouTube video with the
expected media, metadata, and thumbnail.

## Step 8 - publish Facebook chunks and comments

Goal: publish every vertical chunk to one administered Facebook Page.

- [ ] Complete the Facebook items in [Platform sandbox](platform_sandbox/todo.md).
- [ ] Complete [Facebook chunk publishing](facebook_chunk_publishing/todo.md).
- [ ] Publish all three reference chunks as separate Page posts.
- [ ] Attach configured thanks text after each successful video post.
- [ ] Test the optional QA or donation image comment.
- [ ] Keep affiliate-link actions disabled until a valid link is available.
- [ ] Retry a failed comment without re-uploading its video.

Evidence:

- Pending.

Done when: one approved campaign creates three Page video posts and every
configured child comment action has an independent result.

## Step 9 - deliver TikTok chunk drafts

Goal: send every vertical chunk to one TikTok inbox without claiming public
publication.

- [ ] Complete the TikTok items in [Platform sandbox](platform_sandbox/todo.md).
- [ ] Complete [TikTok chunk drafts](tiktok_chunk_drafts/todo.md).
- [ ] Deliver all three reference chunks as separate inbox drafts.
- [ ] Track pending-share capacity before starting each upload.
- [ ] Persist `publish_id`, upload expiry, and final delivery status.
- [ ] Provide copyable caption and hashtag text for manual completion.
- [ ] Confirm that no whole-video TikTok target can be created.

Evidence:

- Pending.

Done when: all three chunks appear as drafts and manual publication remains
clearly outstanding.

## Step 10 - add Shopee, multiple accounts, and operations

Goal: add commerce and scale after the one-account contract works.

- [ ] Complete [Shopee affiliate catalog](shopee_affiliate_catalog/todo.md).
- [ ] Add an approved Shopee link to Facebook comment previews and actions.
- [ ] Expand from one mock brand profile to enough profiles/accounts to manage
  5-10 accounts per platform.
- [ ] Verify exact fan-out counts for multiple accounts.
- [ ] Complete [Operations dashboard](operations_dashboard/todo.md).
- [ ] Complete [Reliability, observability, and retention](reliability_observability_retention/todo.md).
- [ ] Run backup, restore, token-expiry, quota, rate-limit, lost-callback, and
  partial-failure drills.

Evidence:

- Pending.

Done when: multiple accounts operate with visible state, recoverable failures,
protected credentials, and no cross-account mix-ups.

## Dependency path

```mermaid
flowchart LR
    A[Freeze workflow] --> B[Acceptance contract]
    B --> C[Source validation]
    C --> D[Chunk metadata]
    D --> E[Branded media variants]
    E --> F[Manifest gate]
    F --> G[Data model and handoff]
    G --> H[Review and Telegram]
    H --> I[Queue with fake adapters]
    I --> J[YouTube private]
    J --> K[Facebook chunks]
    K --> L[TikTok drafts]
    L --> M[Shopee and multi-account]
    M --> N[Operations hardening]
```

## MVP acceptance run

The first complete MVP run uses one source that produces three chunks and one
selected account on each platform. It must:

- [ ] Produce one validated 1920x1080 whole asset.
- [ ] Produce three validated 1080x1920 chunk assets.
- [ ] Generate one YouTube metadata set and three chunk-specific metadata sets.
- [ ] Apply the mock brand watermark and signature music to both ratios.
- [ ] Show seven targets before approval.
- [ ] Consume one Telegram approval exactly once.
- [ ] Publish one private YouTube video.
- [ ] Publish three Facebook Page videos and configured comment actions.
- [ ] Deliver three TikTok inbox drafts.
- [ ] Survive a worker restart without losing or duplicating work.
- [ ] Show every target and child action with its real final state.

Acceptance evidence:

- Pending.
