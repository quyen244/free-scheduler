# Confirmed product decisions

Last updated: 2026-09-16

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

- YouTube receives the whole video as 16:9. Facebook and TikTok receive unique,
  non-overlapping 9:16 chunks.
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
- The brand profile owns the watermark and signature music used on both 16:9 and
  9:16 outputs.
- Render reusable clean masters first, then derive separate branded variants.
  A brand's vertical variant may be reused by that brand's Facebook and TikTok
  accounts.
- Use mock brand/music configuration until real brand assets are supplied.
- Preserve the original YouTube visuals while replacing audio with the edited
  Vietnamese voice, adding subtitles, the brand watermark, and signature music.
- Mix signature music as a quiet background bed. The mock profile uses a
  `-24 dB` base gain, loops for the asset duration, fades in for `0.75 s`, and
  fades out for `1.0 s`. Speech-aware compression uses threshold `0.02`, ratio
  `8:1`, `20 ms` attack, and `450 ms` release so music falls during narration
  and rises gently in gaps. A missing configured music file rejects the render
  before FFmpeg starts; replacing the file and retrying the render stage reuses
  verified assets.
- TikTok uploads each 9:16 chunk as a draft for the MVP. Prepared caption and
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
- The presenter (host) video belongs to the brand, not to a shared preset. Each
  brand uploads its own presenter and RVM matting runs once at upload, storing a
  derived alpha video beside the original. The original stays immutable.
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
- `clean_lineage` is the original two-stage topology: one `clean_whole`, `N`
  `clean_vertical`, and per brand one `branded_whole` and `N` `branded_vertical`
  whose `lineage_asset_id` points at the clean master they derive from. It
  remains supported for the legacy library presets and mock brand profiles.
- `brand_owned` is the single-pass topology for `brand.v1` layouts: per brand
  one `branded_whole` and `N` `branded_vertical` rendered straight from the
  source, with no clean assets and no lineage. A brand-owned manifest records
  `brand_revisions` so the exact published brand revision behind every asset is
  auditable and an approval can be invalidated when a brand is republished.
- A brand-owned render job must name an explicit brand revision per brand. It
  must not consume a draft or an unversioned "latest" configuration, for the
  same reason a visual preset could not.
- The expected asset count per source is unchanged from the delivery contract:
  a `brand_owned` revision still produces `brands * (1 + N)` delivery assets.
  Only the intermediate clean masters disappear.

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

## Pending decisions

- Whether Shopee Affiliate Open API credentials are available.
