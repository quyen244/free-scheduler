# Confirmed product decisions

Last updated: 2026-09-17

## Source input

- Accept a YouTube URL for the MVP. Local-file upload/import is explicitly
  deferred and must not block the pipeline-correctness milestone.
- Telegram/webhook submission contains only the YouTube URL. Do not require a
  rights marker, rights status, or rights evidence field.
- Reject corrupt media, media shorter than 5 minutes or longer than 20 minutes,
  and media below 720p.
- Normal source duration is 10-20 minutes.
- Identify a YouTube source by normalized YouTube video ID and downloaded SHA-256.
  A future local-import feature would use SHA-256 without changing campaign identity.
- Duplicate protection prevents an accidental second active campaign; it does
  not permanently blacklist a source. Failed work can resume, and an operator
  can intentionally create a new campaign for an old source.
- For the URL-only MVP, `SourceVideo`, `Campaign`, and `ProcessingAttempt`
  provide sufficient source and work history. A separate `SourceSubmission`
  table is deferred until multiple input methods or submission-level auditing
  require it.

## Content and metadata

- All delivery video is 16:9 (1920x1080). YouTube receives the complete
  landscape video. Facebook and TikTok each receive unique, non-overlapping
  landscape chunks cut from that rendered landscape video.
- Chunk boundaries and count continue to use the existing balanced,
  sentence-safe chunking algorithm; only the delivery aspect ratio and render
  lineage change.
- Chunk count is variable. Sources from 5-9 minutes produce one chunk. Longer
  sources produce balanced chunks targeting 4-5 minutes with no tiny
  remainder. When no chunk count can satisfy that range (currently sources
  from 601-719 seconds), use the closest balanced complete partition; never
  drop or overlap source content merely to force the duration range.
- A boundary may move up to 15 seconds to the nearest transcript segment ending
  so speech is not cut mid-sentence.
- Chunk display names are `part_1`, `part_2`, and so on.
- Do not use AI to reject or rank chunks for the MVP.
- Generate a whole-video YouTube title, description, hashtags, and thumbnail
  text.
- Generate a hook, supporting video caption, Facebook caption/hashtags, and
  TikTok caption/hashtags for every chunk.
- Generate titles, captions, hooks, descriptions, and thumbnail text in
  Vietnamese by default.
- Use a curiosity-driven, engaging writing tone, but never misrepresent the
  source, fabricate a claim, or use a misleading hook.
- Use at most two relevant emojis in each platform metadata object. Zero emojis
  is valid when they would feel forced or reduce clarity.
- Chunk metadata uses the chunk transcript plus a whole-video summary. Generated
  claims must remain grounded in the source.
- Validate structured generation output and retry invalid output, but do not
  reject reasonable creative wording through overly strict style rules.
- Store one selected metadata result per revision. Operators may edit it before
  approval.
- Generate exactly five hashtags per platform metadata object: three
  content-specific tags and two relevant broad/discovery tags. Examples include
  `#trending`, `#trend`, `#viral`, `#fyp`, and `#xuhuong`; do not add
  a space after `#` and do not use irrelevant trends.
- An OpenAI API key is available under the server-only environment variable
  `OPENAI_API_KEY`. Never store its value in Git, workflow JSON, browser code,
  logs, or the database.
- Use `gpt-5.6-luna` for metadata generation. The user explicitly selected it
  on 2026-09-10. It must still pass the structured-output, Vietnamese-writing,
  grounding, and retry acceptance fixtures before production use. Access and
  structured generation were verified with the updated API project on
  2026-09-10. Use `reasoning.effort: none` for this focused generation task.
- Keep the `$0.02` estimated per-campaign metadata threshold as an advisory
  warning, not a hard stop. Do not abandon a partially generated campaign only
  because that threshold is crossed.

## Brands, approval, and publishing

- A brand profile groups its YouTube, Facebook, and TikTok accounts.
- The brand profile owns the watermark and signature music used on all 16:9
  delivery outputs (whole video and chunks).
- Render reusable clean masters first, then derive separate branded variants.
  A brand's vertical variant may be reused by that brand's Facebook and TikTok
  accounts.
- Use mock brand/music configuration until real brand assets are supplied.
- Preserve the original YouTube visuals while replacing audio with the edited
  Vietnamese voice, adding subtitles, the brand watermark, and signature music.
- Mix signature music as a quiet background bed. The mock profile uses a
  `-24 dB` base gain, loops for the asset duration, fades in for `0.75 s`, and
  fades out for `1.0 s`. A missing configured music file rejects the render
  before FFmpeg starts; replacing the file and retrying the render stage reuses
  verified assets. The mock profile's ducking numbers (threshold `0.02`, ratio
  `8:1`, `450 ms` release) are **superseded** - see "Signature music level"
  below for why they made the bed inaudible.
- TikTok uploads each 16:9 chunk as a draft for the MVP. Prepared caption and
  hashtags are copied during manual draft completion.
- One Telegram approval temporarily authorizes the complete campaign revision
  across all selected brand profiles and platforms.
- Any asset, metadata, product, schedule, target account, or brand-profile change
  creates a new revision and invalidates prior approval.
- Editing post metadata does not rerender video. Editing visual text, subtitles,
  music, watermark, or media settings rerenders affected variants before a new
  approval can be requested.
- The operator-facing manual metadata edit surface remains in roadmap Step 5,
  where the app owns review and approval invalidation; it is not part of Step 2.

## Brand-owned layouts (supersedes the visual-preset split)

- A brand owns its complete visual layout. `automation/data/presets/brand/<id>/`
  holds one `config.json` plus that brand's own uploaded files in `assets/`.
  The separate "visual preset" object is no longer the unit an operator edits.
- A layout is a z-ordered list of layers on an initially empty canvas for each
  ratio (`vertical_9_16` 1080x1920 and `landscape_16_9` 1920x1080). Layer kinds
  are `image`, `main_video`, `blur`, `host`, `text`, and `subtitle`. A new brand
  starts as a black canvas with no layers; every layer present was placed by an
  operator.
- A blur region takes part in the layer stack like any other layer. Its
  rectangle stays normalised against the **source frame**, because a blur hides
  something inside the footage and must follow it when the source channel
  changes resolution, but its `z` decides when it is applied: a blur above the
  logo blurs the logo, a blur below it does not. This supersedes the earlier
  rule that a blur has no `z` and is always applied to the source pixels before
  compositing. A blur layer with no `z` keeps the old meaning, so revisions
  published before this decision render exactly as they did.
- The presenter (host) video belongs to the brand, not to a shared preset. Each
  brand uploads its own presenter and RVM matting runs once at upload, storing a
  derived alpha video beside the original. The original stays immutable.
- A role is a label on an asset, not a slot. A brand may hold any number of
  logos or watermarks, and may place the same asset into a layout as many times
  as it likes; each upload and each placement gets its own free id. Only `host`
  and `main_video` stay one per layout, because the renderer composites exactly
  one of each.
- Publishing a brand draft creates a numbered, immutable revision carrying a
  `content_sha256`. A draft is never a valid render input.
- Because a brand owns the footage rectangle, the blur regions, and the
  subtitle, brands no longer share a clean master. A brand-owned revision
  renders every delivery asset once, directly from the source, per brand. This
  replaces the earlier "render reusable clean masters first, then derive
  branded variants" rule for brand-owned renders; see the topology decision
  below.
- The old `presets/preset-editor/` and `presets/brand-assets/` data is retained
  as history. It is migrated, not deleted.

## Render topology

- A media manifest declares its `topology` explicitly.
- New renders first create one branded 1920x1080 whole asset per brand, then
  create `N` 1920x1080 chunk assets by frame-accurately cutting that same
  rendered file at the existing chunk boundaries. Each chunk records the whole
  asset as lineage, so subtitles, voice, watermark, and signature music match
  the corresponding section of the YouTube asset exactly.
- `clean_lineage` and `brand_owned` remain historical topology names during the
  migration. Existing v1 manifests and assets remain immutable evidence; new
  landscape-chunk renders use a versioned manifest contract.
- A brand-owned render job must name an explicit brand revision per brand. It
  must not consume a draft, for the same reason a visual preset could not.
- A caller may ask for `"latest"` instead of a number. The render service
  resolves it once, when the job is accepted, to the highest published revision
  of that brand, answers with the number it chose, and records that number in
  the manifest. The floating word is never stored and never reaches a renderer,
  so a manifest still names the exact layout behind every asset and a rendered
  campaign still cannot change meaning when someone republishes the brand. This
  narrows, rather than removes, the earlier prohibition on "latest": what was
  forbidden — an unversioned reference travelling into stored state — stays
  forbidden.
- `"latest"` never falls back to a draft. A brand with no published revision
  refuses the job, because the alternative is a render that silently uses a
  layout nobody approved.
- The expected delivery asset count per source remains `brands * (1 + N)`:
  one landscape whole asset plus one landscape chunk asset per chunk and brand.

## Visual presets and host matting (historical; superseded by brand-owned layouts)

- A visual preset is a versioned, immutable render configuration. It contains
  only allowlisted asset references and normalized layout values; it never
  embeds arbitrary filesystem paths or credentials.
- Operators edit a draft preset in the local UI for both `vertical_9_16`
  (1080x1920) and `landscape_16_9` (1920x1080). Publishing a draft creates a
  new numbered preset revision; a published revision is not edited in place.
- The workflow selects an explicit `preset_id` and `preset_revision`. It must
  not consume an editor draft or an unversioned "latest" configuration.
- The render service validates a published preset revision before accepting a
  job. A visual-preset change is a render-affecting change and therefore
  requires new assets and a new campaign approval when campaign approval exists.
- Human host video is processed by Robust Video Matting (RVM MobileNetV3) in
  the existing GPU render service. The original uploaded host remains
  immutable; the service writes a derived alpha video and alpha preview. The
  editor may later add correction masks, but a correction is saved as a new
  matting revision rather than overwriting the derived alpha.
- The first editor is local-operator tooling. Its preview is explicitly marked
  as a preview; render-service/FFmpeg remains the source of truth for the final
  media assets and media manifest.
- The editor UI runs as the localhost-only `ui` Docker Compose service. It
  shares `/data` with the render service and reaches that service only over the
  internal Compose network; operators do not run Node/npm from Windows or WSL
  to use the editor.

## Failure and retention

- Retry transient failures according to error type. After automatic retries are
  exhausted, pause the campaign in `needs_action` rather than starting over.
- Media ingest uses three total attempts for download and audio-extraction
  failures, waiting 5 seconds and then 20 seconds. Invalid URLs and source
  policy failures are not retried. An exhausted transient failure remains
  operator-retryable from the ingest stage.
- If a required chunk fails, the whole campaign remains unapprovable.
- Record every internal failure. Telegram alerts only when operator action is
  required, followed by one final campaign summary.
- Allow `Retry from failed stage` and `Restart as new campaign` actions.
- Delete temporary files one day after successful publishing, final/source files
  seven days after publishing, and failed-campaign files three days after the
  last failure. Keep metadata and audit history until manual deletion.
- Never delete assets referenced by active, approved, queued, or publishing work.
- Preserve the unreferenced corrupt legacy media file and pre-planner chunk row
  for now as diagnostic history. Do not reprocess or delete them during Step 2.

## System ownership

- n8n owns orchestration; Docker services own testable media and metadata rules;
  the Next.js app owns review and publishing state; a worker calls platform APIs.
- PostgreSQL is the initial durable application/publish queue. Redis is deferred
  until load proves it necessary.
- Official APIs are preferred. Browser/cookie automation is not an MVP path.
- Keep durable n8n-to-app campaign-control wiring in roadmap Step 4. Step 2 does
  not pull the signed handoff into the media-production boundary.

## Step 2 verification boundary

- Accept the layered duration evidence for Step 2: planner/topology coverage at
  5:00, 9:00, 10:00, 13:42, and 20:00; short real-FFmpeg asset coverage; and one
  representative 682.841-second full render. Full encodes of all five matrix
  durations are not required for Step 2 closure.
- Defer a live n8n execution with possible Telegram side effects to the signed
  handoff milestone. The imported inactive workflow plus canonical/live-export
  contract tests are sufficient for Step 2.

## Selective render previews

- A root-level `render-plan.yaml` is local operator input for a selective
  render preview. It is not a delivery manifest, is never read directly by the
  render service, and contains no credentials. A local runner validates it and
  sends a typed request to the render-service preview endpoint.
- A plan selects one or more brand revisions and `all`, `landscape`, or
  `vertical` variants. It may further select one or more one-based chunk
  indices for the vertical variant, such as `chunks: [1]`. `all` includes the
  whole landscape asset and only the selected vertical chunks when a chunk list
  is present.
- Preview outputs are kept outside delivery revision paths and never update
  `media-manifest.json`, campaign state, approval state, or the `rendered`
  pipeline stage. A preview cannot be published.
- A brand may use distinct background assets in its landscape and vertical
  layouts. A layout change creates a new immutable brand revision.
- Signature music is one optional, versioned brand-level asset shared by both
  layouts for now. It has explicit loop, gain, fade, and speech-ducking
  settings; separate music by ratio is out of scope until explicitly decided.

## Signature music level (confirmed 2026-09-17)

- **The target is a ratio to the narration, not an absolute gain.** The bed sits
  at a flat **45 %** of narration level, whether or not the host is speaking.
  Reached in two operator passes: v12 was inaudible, a 30 % / 45 % ducked mix
  was audible but still too quiet under speech, and the confirmed answer is
  **no dip at all**.
- `volume_db` is absolute gain on the music file, so the gain that lands on 45 %
  depends on how that track was mastered. It is a per-brand number, derived
  rather than copied between brands:
  `volume_db = 20*log10(0.45 * rms(narration) / rms(music at unity))`.
- **The schema ceiling moved from `-12` to `0` dB** in `brand.py:47` and
  `brands.py:314`. The old ceiling was a proxy for "stay under the voice" from
  when the mock profile sat at `-24 dB`, and it refused the `-7.7 dB` that
  `an-so`'s quiet master needs. What remains is an anti-clipping bound only; the
  mix ends in `alimiter=limit=0.98`.
- **The prior ducked profile could not breathe between phrases.** Two
  mock-profile settings prevented it:
  threshold `0.02` (-34 dBFS) sits 14.1 dB *below* the narration, so at ratio
  `8:1` the compressor held ~12.4 dB of gain reduction permanently; and release
  `450 ms` outlives the 100-200 ms pauses in Vietnamese narration, so the bed
  never recovered inside a sentence. Measured on the v12 render the bed was
  **quieter in pauses than under speech** (-4.2 dB spread) - the opposite of the
  intent.
- Confirmed settings for `an-so`: `volume_db -7.9`, ratio **`1.0`**. Measured on
  the rendered file: **44 % speaking, 45 % in pauses, +0.1 dB spread**. The
  44 % is `alimiter=limit=0.98` working marginally harder now that voice and bed
  sum louder - 0.2 dB, inaudible.
- **`an-so` ends on the flat 45 % bed.** The music-only RMS peak control was
  implemented and measured (85.1 % to 30.9 % in high-music windows), but the
  operator found it too quiet and explicitly selected the 45 % flat version on
  2026-09-17. `peak_control` is therefore absent from the `an-so` draft. It
  remains an optional per-brand feature for a future music file, and still
  never uses narration as its trigger.
- **`ratio 1.0` is how ducking is switched off.** `SpeechDucking.enabled` is
  `Literal[True]` in the schema and `_music_graph` always emits
  `sidechaincompress`, but ratio `1.0` is unity gain, so the filter stays in the
  graph and does nothing. No schema change was needed. `threshold`, `attack_ms`
  and `release_ms` are then inert and kept only as a record of the last ducked
  tuning.
- The ducked 30 % / 45 % mix is kept as evidence at
  `automation/data/previews/an-so-music-tuned-20s.mp4` in case a future brand
  wants the bed to breathe; the settings that produced it were `volume_db -7.7`,
  threshold `0.025`, ratio `3.0`, attack `20 ms`, release `200 ms`.
- **How to measure.** Speech and music are uncorrelated, so per-frame
  `sqrt(p(mix) - p(voice))` recovers the surviving bed from a finished mp4.
  Divide that by the same graph run with ducking bypassed (`threshold 1.0,
  ratio 1.0`) before reading a percentage, or the music's own swells are
  mistaken for ducking. Sample-level correlation is useless after AAC - use the
  envelope. Tool: `automation/data/_tools/duck_sweep.py`.

## Vietnamese voice engine

- The render service speaks Vietnamese with **VieNeu-TTS v3 Turbo** on CUDA,
  batched at 32, using the **Minh Quân** preset. This replaces ZeroTTS, decided
  2026-09-17 on measured evidence: on 64 real cues from the project's own
  transcript, VieNeu at batch 32 ran at RTF 20.62x against ZeroTTS's 0.86x in
  its shipped configuration. Evidence:
  `reports/vieneu-tts-gpu-benchmark-2026-09-17.md`.
- Batch 32 is the chosen size because the throughput curve is flat past it
  while VRAM roughly doubles. Peak VRAM at 32 is ~2.0 GB of 6.0 GB.
- **The render-service GPU image is no longer torch-free.** `vieneu[cuda]`
  brings torch, torchaudio and transformers. This supersedes the earlier
  premise that ONNX-without-torch was required to keep the GPU free for other
  work: a render holds ~1.1 GB and VieNeu ~2.0 GB of 6.0 GB, so both fit.
- **The voice namespace changed and is not migrated.** ZeroTTS's eight voices
  and VieNeu's twenty-five do not overlap, and `maichi` does not exist in
  VieNeu. Voice manifests naming a ZeroTTS voice are kept as history; the
  tracks they describe cannot be reproduced by the current engine. Re-voicing a
  campaign that already has approval creates a new revision, unchanged from the
  general rule above.
- Audio quality was never benchmarked, only speed. Operator acceptance of how
  the new voice sounds is a required gate, tracked in
  `features/vietnamese_voice_engine/todo.md`.

## On-screen title (frozen)

- **Confirmed 2026-09-17: the renderer draws no title.** A re-up carries its
  title in the platform post — YouTube's title field, the Facebook post body,
  the TikTok caption — so burning one into the frame duplicates it and costs
  picture. This reverses the earlier assumption that every delivery asset shows
  its title.
- The freeze is a **service-wide switch**, `RENDER_FROZEN_TEXT`, defaulting to
  `title`. It names text bindings or layer ids the renderer refuses to draw.
- **Layouts keep their title layer.** Freezing is a rendering decision, not a
  brand edit: position, size and colour survive untouched, so bringing titles
  back is `RENDER_FROZEN_TEXT=` (empty) and no brand revision. This is why the
  switch lives in the service and not in each brand's JSON.
- A frozen layer is recorded on the asset's warnings, so a missing title is
  explicable from the manifest rather than read as a render fault.
- Evidence: `tests/test_brand_compose.py::TestFrozenText`, and the published
  `an-so` revision 11 composing 0 `drawtext` steps in both aspects while both
  layouts still list their `title` layer.

## Pending decisions

- Whether Shopee Affiliate Open API credentials are available.
