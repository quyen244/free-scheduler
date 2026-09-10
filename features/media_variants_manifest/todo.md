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
- [ ] Preserve the current vertical preset for per-chunk output.
- [ ] Apply the selected brand watermark and signature music to both ratios.
- [ ] Reuse one same-brand vertical asset for Facebook and TikTok.
- [-] Record SHA-256, duration, dimensions, codec, bytes, and warnings. The
  probe/hash contract is implemented and Docker-tested; production render jobs
  do not emit it yet.
- [-] Record brand profile, render revision, content item, and clean-master
  lineage. The manifest contract is implemented; app campaign revision linkage
  starts at the later signed handoff boundary.
- [-] Add preflight checks for missing/corrupt/mismatched outputs. The validator
  rejects missing/empty media, unreadable probes, absent streams, wrong ratios,
  duration drift, and non-H.264/AAC output; render completion is not wired yet.
- [ ] Test 5-, 9-, 10-, 13:42-, and 20-minute fixtures in Docker.
- [ ] Test two brands never share branded paths or signature music.
- [ ] Visually inspect representative frames for both layouts.
