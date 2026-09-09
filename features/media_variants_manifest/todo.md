# Media variants and manifest — todo

- [ ] Define the versioned media-manifest JSON schema.
- [ ] Define stable paths for whole landscape and vertical chunks.
- [ ] Define stable clean-master and `brand/<brand_id>/revision/<n>` variant paths.
- [ ] Enforce one chunk for 5-9 minute sources and balanced 4-5 minute chunks otherwise.
- [ ] Preserve transcript-segment boundaries within the 15-second adjustment window.
- [ ] Name chunks `part_1`, `part_2`, and so on without gaps or overlap.
- [ ] Add or adapt the `yt-landscape` preset.
- [ ] Preserve the current vertical preset for per-chunk output.
- [ ] Apply the selected brand watermark and signature music to both ratios.
- [ ] Reuse one same-brand vertical asset for Facebook and TikTok.
- [ ] Record SHA-256, duration, dimensions, codec, bytes, and warnings.
- [ ] Record brand profile, campaign revision, content item, and clean-master lineage.
- [ ] Add preflight checks for missing/corrupt/mismatched outputs.
- [ ] Test 5-, 9-, 10-, 13:42-, and 20-minute fixtures in Docker.
- [ ] Test two brands never share branded paths or signature music.
- [ ] Visually inspect representative frames for both layouts.
