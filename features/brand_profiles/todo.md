# Brand profiles and branded media variants - todo

## Contract

- [x] Confirm a brand groups YouTube, Facebook, and TikTok accounts.
- [x] Confirm the same watermark and signature music apply to 16:9 and 9:16 outputs.
- [x] Confirm clean masters plus per-brand variants.
- [x] Confirm same-brand Facebook and TikTok may reuse one vertical variant.
- [x] Confirm a temporary mock brand/music configuration.
- [x] Confirm signature music is a quiet background bed.
- [x] Confirm automatic speech ducking is desired when practical.
- [x] Calibrate loudness, ducking threshold/gain, looping, fades, and
  missing-file behavior against a fixture. Evidence:
  `test_signature_music_ducks_loops_and_fades_without_lowering_speech` and
  `test_missing_signature_music_fails_during_brand_preflight`.

## Mock pipeline profile

- [x] Add an allowlisted `data/music/` convention without committing unlicensed music.
  Evidence: `library.music_path()` allows only a basename under `/data/music`;
  `mock-signature.wav` is generated locally and remains git-ignored.
- [x] Add one mock brand preset referencing placeholder watermark and music paths.
  Evidence: `data/presets/brands/mock-brand.json`.
- [x] Validate brand paths remain inside the mounted media/preset roots.
  Evidence: `test_brand_music_path_cannot_escape_allowlisted_folder`.
- [x] Render mock-branded 16:9 and 9:16 fixtures. Evidence:
  `/media-revision/jobs` fixture job `f4f842d3e00c4288a3c5fa464351ed94`.

## Durable app model

- [ ] Add `BrandProfile`, `BrandAccount`, and `BrandMediaAsset` models.
- [ ] Ensure a social account has an explicit brand assignment.
- [ ] Add brand-profile selection and fan-out preview.
- [ ] Version brand media/configuration and invalidate affected approvals/renders on edit.
- [ ] Test two brands using one source without path, credential, music, or watermark crossover.
