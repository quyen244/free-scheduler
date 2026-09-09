# Brand profiles and branded media variants - todo

## Contract

- [x] Confirm a brand groups YouTube, Facebook, and TikTok accounts.
- [x] Confirm the same watermark and signature music apply to 16:9 and 9:16 outputs.
- [x] Confirm clean masters plus per-brand variants.
- [x] Confirm same-brand Facebook and TikTok may reuse one vertical variant.
- [x] Confirm a temporary mock brand/music configuration.
- [x] Confirm signature music is a quiet background bed.
- [x] Confirm automatic speech ducking is desired when practical.
- [ ] Calibrate loudness, ducking threshold/gain, looping, fades, and
  missing-file behavior against a fixture.

## Mock pipeline profile

- [ ] Add an allowlisted `data/music/` convention without committing unlicensed music.
- [ ] Add one mock brand preset referencing placeholder watermark and music paths.
- [ ] Validate brand paths remain inside the mounted media/preset roots.
- [ ] Render mock-branded 16:9 and 9:16 fixtures.

## Durable app model

- [ ] Add `BrandProfile`, `BrandAccount`, and `BrandMediaAsset` models.
- [ ] Ensure a social account has an explicit brand assignment.
- [ ] Add brand-profile selection and fan-out preview.
- [ ] Version brand media/configuration and invalidate affected approvals/renders on edit.
- [ ] Test two brands using one source without path, credential, music, or watermark crossover.
