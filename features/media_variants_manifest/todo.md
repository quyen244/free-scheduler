# Media variants and manifest — todo

- [x] Define the versioned media-manifest JSON schema. Evidence:
  `render-service/contracts/media-manifest.v1.schema.json` and schema parity test.
- [x] Define stable paths for whole landscape and vertical chunks. Evidence:
  `test_manifest.py::test_paths_are_revisioned_and_platform_neutral`.
- [x] Define revisioned clean-master and
  `brands/<brand_id>/revision/<n>` variant paths. Revisioning clean paths keeps
  historical manifest checksums truthful after a source/voice/layout change.
- [x] Enforce one chunk for 5-9 minute sources and balanced 4-5 minute chunks otherwise.
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
- [ ] Test 5-, 9-, 10-, 13:42-, and 20-minute fixtures in Docker.
- [x] Test two brands never share branded paths or signature music. Evidence:
  `test_clean_vertical_and_brand.py::test_two_brands_keep_separate_paths_and_signature_music`.
- [x] Visually inspect representative frames for both layouts. Evidence:
  `reports/step2-revision3-branded-whole-frame.jpg` and
  `reports/step2-revision3-branded-part1-frame.jpg`.
