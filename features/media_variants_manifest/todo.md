# Media variants and manifest — todo

- [x] Define the versioned media-manifest JSON schema. Evidence:
  `render-service/contracts/media-manifest.v1.schema.json` and schema parity test.
- [x] Define stable paths for whole landscape and vertical chunks. Evidence:
  `test_manifest.py::test_paths_are_revisioned_and_platform_neutral`.
- [x] Define revisioned clean-master and
  `brands/<brand_id>/revision/<n>` variant paths. Revisioning clean paths keeps
  historical manifest checksums truthful after a source/voice/layout change.
- [x] Enforce one chunk for 5-9 minute sources and balanced chunks targeting
  4-5 minutes otherwise, including the accepted closest-partition behavior for
  the unreachable 601-719-second band.
- [x] Preserve transcript-segment boundaries within the 15-second adjustment window.
- [x] Name chunks `part_1`, `part_2`, and so on without gaps or overlap.
- [x] Add the clean `yt-landscape` preset and direct whole-source renderer.
  Evidence: `test_clean_landscape.py` plus the 19-second fixture probe/frame.
- [x] Preserve the current vertical preset for per-chunk output. Evidence:
  `test_clean_vertical_and_brand.py::test_clean_part_then_mock_brand_adds_watermark_and_music`.
- [x] Apply the selected brand watermark and signature music to both ratios.
  Evidence: `/media-revision/jobs` fixture job
  `f4f842d3e00c4288a3c5fa464351ed94`.
- [x] Reuse one same-brand vertical asset for Facebook and TikTok. Evidence:
  `test_clean_vertical_and_brand.py::test_complete_revision_creates_every_part_and_reuses_same_brand_verticals`.
- [x] Record SHA-256, duration, dimensions, codec, bytes, and warnings.
  Evidence: `20 passed` for `test_manifest.py`, `test_clean_landscape.py`,
  and `test_clean_vertical_and_brand.py`; production fixture revision 3 wrote
  four probed assets with zero manifest failures.
- [x] Record brand profile, render revision, content item, and clean-master
  lineage. Evidence: `media-manifest.json` for `jNQXAC9IVRw` revision 3 and
  the two-brand isolation test.
- [x] Add preflight checks for missing/corrupt/mismatched outputs. Evidence:
  endpoint preflight rejects missing brand music before queueing, and the
  manifest validator rejects invalid assets before `ready`.
  `test_corrupt_brand_asset_retry_reuses_verified_peers` proves the same
  revision repairs only the corrupt asset and preserves verified peers.
- [x] Verify the 5-, 9-, 10-, 13:42-, and 20-minute matrix with the accepted
  layered strategy: planner/topology coverage for every duration, short real
  FFmpeg assets, and one 682.841-second full render. The user confirmed on
  2026-09-11 that five separate full encodes are not required for Step 2.
- [x] Test two brands never share branded paths or signature music. Evidence:
  `test_clean_vertical_and_brand.py::test_two_brands_keep_separate_paths_and_signature_music`.
- [x] Visually inspect representative frames for both layouts. Evidence:
  `reports/step2-revision3-branded-whole-frame.jpg` and
  `reports/step2-revision3-branded-part1-frame.jpg`.

## Landscape chunk delivery — `media-manifest.v2` (2026-09-17)

Implements `landscape_chunk_delivery_proposal.md`. Every delivery asset is
1920x1080; a chunk is a frame-accurate cut of its own brand's whole.

- [x] Define v2 roles, paths, lineage validation, and v1 read compatibility.
  Evidence: `manifest.py` `branded_landscape_chunk` role, `SCHEMA_VERSION`
  carried into `deterministic_asset_id`, `contracts/media-manifest.v2.schema.json`
  generated in the container; `tests/test_landscape_chunks.py` and
  `tests/test_manifest.py::test_the_v1_contract_is_frozen_history`.
- [x] Fix `_validate_ready_topology`, which added v1 `branded_vertical`
  expectations on top of the v2 set, so no v2 revision could ever reach
  `ready`. Evidence: `TestTopology` in `tests/test_landscape_chunks.py`; the
  real revision below reaches `ready`.
- [x] Replace vertical delivery targets with one landscape whole plus
  frame-accurate cuts, keeping retry/resume. Evidence:
  `variants.render_brand_revision`, `render.cut_branded_landscape_chunk`
  (`-ss` after `-i`, re-encode, never `-c copy`).
- [x] Cut preview chunks from the preview whole instead of from source, so a
  preview carries the shipping music bed. Evidence:
  `variants.render_brand_preview`; `tests/test_preview_jobs.py`
  `test_a_chunks_only_preview_still_cuts_from_a_real_whole`,
  `test_a_failed_whole_fails_every_chunk_that_depended_on_it`.
- [x] Update the acceptance contract validator and fixtures. Evidence:
  `automation/acceptance-contract/contract.py` snapshots are `step1.v2` with
  `assets.chunks` at 1920x1080 and a required `lineage_asset_id`;
  `python -m unittest discover -s automation/acceptance-contract`, `16 passed`.
- [x] Run a real representative render with probe, checksum and frame evidence.
  Evidence: `hS3VXBeEv0I`, brand `an-so` revision 13, throwaway render
  revision 9002 — `media-manifest.v2` / `landscape_chunks` / `state=ready`,
  4 assets, 0 failures, 554.2 s wall for 1375 s of media (2.48x realtime).
  Every asset re-probed from disk: 1920x1080 h264/aac, 24000/1001 fps, audio
  and video stream durations within 0.04 s, sizes and recomputed SHA-256
  matching the manifest, each chunk's duration within 0.05 s of its persisted
  `chunks` span, each chunk's `lineage_asset_id` naming the brand's whole.
- [x] Prove the cut lands on the boundary frame rather than near it. Evidence:
  each chunk's frame 0 scored by SSIM against a +/-3-frame fan of the whole
  around `start_s`; the peak sits on the boundary for all three parts
  (part_1 0.9974, part_2 0.9883, part_3 0.9807, each above its neighbours).
- [x] Visually inspect representative 16:9 frames. Evidence:
  `reports/landscape-chunks-whole-t200-frame.jpg` and
  `reports/landscape-chunks-part2-first-frame.jpg` — the chunk's first frame
  carries the same background, host, watermarks and subtitle treatment as the
  whole. The title layer is absent because `RENDER_FROZEN_TEXT=title`.
- [ ] Record a Facebook Page-video and TikTok inbox-draft preflight against a
  real 1920x1080 chunk. Still unverified: the landscape rules in
  `features/_shared/delivery_contract.md` are read from platform
  documentation, not from a provider response. No publisher adapter exists yet.
