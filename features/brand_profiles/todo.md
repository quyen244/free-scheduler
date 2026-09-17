# Brand profiles and branded media variants - todo

## Contract

- [x] Confirm a brand groups YouTube, Facebook, and TikTok accounts.
- [x] Confirm the same watermark and signature music apply to 16:9 and 9:16 outputs.
- [x] Confirm clean masters plus per-brand variants.
- [x] Confirm same-brand Facebook and TikTok may reuse one vertical variant.
- [x] Confirm a temporary mock brand/music configuration.
- [x] Confirm signature music is a quiet background bed.
- [x] Keep speech ducking configurable; `an-so` deliberately selects no ducking
  (`ratio: 1.0`) for its flat 45 % bed.
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

## Signature music level (2026-09-17)

- [x] Confirm the bed actually reaches the output.
  Reported missing; it was not. In every voice-silent window the rendered audio
  sits exactly `volume_db` under the decoded mp3, tracking its per-window
  level: music −42.5 dBFS against speech −22.5 dBFS at `volume_db: -24.0`.
  Waveform correlation is the wrong test — AAC and loop phase hold it near 0.3
  even when the bed is unquestionably present. Method recorded in `GUIDE.md`.
- [x] Sync the `an-so` draft with the published revision.
  `publish()` validates a payload, not the draft file, so v11 shipped with
  `signature_music` while `config.json` still had `null` — the next publish
  from the visual editor would have silently dropped the bed.
- [x] Build listenable level variants.
  `volume_db` is absolute gain, not a ratio, so it was solved against the
  **active** speech level (100 ms frames above RMS 1e-3): 5/10/15/20/30 % →
  −27.7 / −21.6 / −18.1 / −15.6 / −12.1 dB. The shipped −24.0 is ~8 %. Five
  20-second clips at `automation/data/hS3VXBeEv0I/previews/music-test/`, cut
  from t=470 s (83 % speech, 7 speech→silence transitions) through the same
  `_music_graph` chain, music seeked to `t mod 200 s` so the loop sits where it
  really would. Measured speech-to-gap deltas: 26.6 / 20.5 / 17.0 / 14.5 /
  11.1 dB, peak −1.4 dBFS in all five.
- [x] **Operator selected the level.** Confirmed after listening: a flat 45 %
  bed, `volume_db: -7.9` and `ducking.ratio: 1.0` for `an-so`. The rendered
  20-second preview measured 44 % while speaking and 45 % in pauses (+0.1 dB
  spread); evidence: `automation/data/previews/music-flat45.mp4`. The draft is
  ready to publish as a new immutable brand revision; publishing remains an
  explicit operator action.

## Signature-music peak control (approved 2026-09-17)

- [x] Add `signature_music.peak_control`, which responds to the music track's
  RMS energy only and caps a loud passage at 30 % rather than ducking for voice.
  Contract: `music_peak_control_proposal.md`; schema/graph tests passed.
- [x] Calibrate `Investigative-Silence.mp3` and render ordinary/climactic
  previews. `music-rms30-final.mp4` measured 25.7 % ordinary and 30.9 %
  climactic, but the operator selected the flat 45 % version; `an-so` therefore
  has no `peak_control` in its draft and is ready for listening/publishing.
