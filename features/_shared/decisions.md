# Confirmed product decisions

Last updated: 2026-09-09

## Source and rights

- Accept a YouTube URL for the MVP. Local-file upload/import is explicitly
  deferred and must not block the pipeline-correctness milestone.
- Reject corrupt media, media shorter than 5 minutes or longer than 20 minutes,
  media below 720p, and submissions whose rights status is `unknown`.
- Normal source duration is 10-20 minutes.
- Identify a YouTube source by normalized YouTube video ID and downloaded SHA-256.
  A future local-import feature would use SHA-256 without changing campaign identity.
- Duplicate protection prevents an accidental second active campaign; it does
  not permanently blacklist a source. Failed work can resume, and an operator
  can intentionally create a new campaign for an old source.
- Allowed rights states are `owned`, `licensed`, `permission`, and
  `public_domain`. Store an optional evidence note or URL.

## Content and metadata

- YouTube receives the whole video as 16:9. Facebook and TikTok receive unique,
  non-overlapping 9:16 chunks.
- Chunk count is variable. Sources from 5-9 minutes produce one chunk. Longer
  sources produce balanced 4-5 minute chunks with no tiny remainder.
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
- Select the OpenAI model with a cost-first fixture evaluation. The selected
  model must still pass the structured-output, Vietnamese-writing, grounding,
  and retry acceptance tests; record its exact name and cost before production.

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
- Mix signature music as a quiet background bed. Automatic speech ducking is a
  desired enhancement: lower music while speech is active and allow a gentle
  rise between speech, with loop and fade behavior calibrated against fixtures.
- TikTok uploads each 9:16 chunk as a draft for the MVP. Prepared caption and
  hashtags are copied during manual draft completion.
- One Telegram approval temporarily authorizes the complete campaign revision
  across all selected brand profiles and platforms.
- Any asset, metadata, product, schedule, target account, or brand-profile change
  creates a new revision and invalidates prior approval.
- Editing post metadata does not rerender video. Editing visual text, subtitles,
  music, watermark, or media settings rerenders affected variants before a new
  approval can be requested.

## Failure and retention

- Retry transient failures according to error type. After automatic retries are
  exhausted, pause the campaign in `needs_action` rather than starting over.
- If a required chunk fails, the whole campaign remains unapprovable.
- Record every internal failure. Telegram alerts only when operator action is
  required, followed by one final campaign summary.
- Allow `Retry from failed stage` and `Restart as new campaign` actions.
- Delete temporary files one day after successful publishing, final/source files
  seven days after publishing, and failed-campaign files three days after the
  last failure. Keep metadata and audit history until manual deletion.
- Never delete assets referenced by active, approved, queued, or publishing work.

## System ownership

- n8n owns orchestration; Docker services own testable media and metadata rules;
  the Next.js app owns review and publishing state; a worker calls platform APIs.
- PostgreSQL is the initial durable application/publish queue. Redis is deferred
  until load proves it necessary.
- Official APIs are preferred. Browser/cookie automation is not an MVP path.

## Pending decisions

- Exact OpenAI model and per-campaign cost ceiling after the cost-first fixture
  evaluation.
- Exact signature-music loudness/ducking calibration and behavior when a
  configured audio file is missing.
- Whether Shopee Affiliate Open API credentials are available.
