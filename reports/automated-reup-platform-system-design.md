# Automated re-up platform: system design and implementation plan

Date: 2026-09-09  
Scope inspected:

- Scheduler app: `D:\Projects\Assignment\Free-AI-Social-Media-Scheduler`
- Media automation: `D:\Projects\Assignment\Free-AI-Social-Media-Scheduler\automation`

## 1. Executive recommendation

Keep the two existing projects, but give them sharply different jobs:

- **n8n is the media-production orchestrator.** It owns ingest, transcription,
  translation, voice, chunking, and rendering. It passes IDs and JSON, never
  video bytes.
- **The Next.js app is the control plane and source of truth.** It owns the
  review queue, products, campaigns, platform/account selection, OAuth tokens,
  approvals, publish attempts, comments, history, and the operator dashboard.
- **A separate publisher worker performs uploads.** It claims durable jobs from
  PostgreSQL, streams files from the shared media volume, calls official
  platform APIs, retries transient failures, and records every attempt.
- **Telegram is an approval client, not the workflow engine.** Its buttons call
  the app with a short-lived, one-use approval token. The same approval is also
  visible and revocable in the web UI.

For a local, single-operator installation, start with a **PostgreSQL-backed job
queue and one worker process**. Do not add Redis until concurrency or scheduling
load proves PostgreSQL insufficient. At 5-10 accounts per platform, API quotas,
token correctness, and platform policy are much more likely bottlenecks than
queue throughput.

The first vertical slice should be:

`n8n render complete -> app review item -> Telegram approve -> one YouTube account -> result in dashboard`

Only after this is reliable should Facebook, Shopee selection/comments, TikTok,
and multi-account fan-out be added.

## 2. Important constraint before implementation

This design assumes every source is owned, licensed, public-domain, or used with
permission. Translation, a new voice, subtitles, a border, a logo, or an aspect
ratio change do **not** automatically make a copied video lawful or eligible for
monetization.

There is also a direct TikTok product constraint: TikTok's current Direct Post
guidelines say that an app for copying arbitrary content and an internal upload
utility for accounts managed by one person/team are not acceptable audit use
cases. An unaudited client is restricted to private posts and a small active-user
cap. Therefore, **fully automatic public TikTok Direct Post must be treated as a
feasibility gate, not a promised feature**. The safe initial choices are TikTok
Upload-to-Drafts/manual completion, the existing MuAPI path if its terms and
account model fit, or no TikTok publishing until the use case is accepted.

References:

- [TikTok Direct Post API](https://developers.tiktok.com/docs/en/content-posting-api-reference-direct-post)
- [TikTok content-sharing guidelines](https://developers.tiktok.com/docs/en/content-sharing-guidelines)
- [TikTok authenticity and unoriginal-content rules](https://www.tiktok.com/community-guidelines/en/integrity-authenticity/)
- [YouTube reused and inauthentic-content policy](https://support.google.com/youtube/answer/1311392?hl=en)

## 3. What exists now

### 3.1 n8n/media pipeline audit

The checked-in latest workflow is `workflows/f7-render.json`. It contains 24
nodes and implements:

`Webhook -> async ingest -> transcribe -> conditional translate -> chunk -> async voice -> async render`

What is already strong:

- Media stays on a shared volume; n8n passes only identifiers and JSON.
- Long operations use `202 + job_id + callback + Wait`, avoiding HTTP timeouts.
- Ingest, translation, voice, and render all have explicit failure branches.
- SQLite WAL records `videos`, `chunks`, and `jobs`.
- Stages only move forward, enabling cheap retries.
- The renderer has verified 1080x1920 output, per-chunk files, subtitles,
  Vietnamese voice, artwork/preset composition, and a concatenated final video.
- Existing Docker-based test records report 93 passing tests across services.

What is not built:

- F8 caption generation, durable review/approval, and notification.
- Platform publishing.
- A 16:9 render variant for YouTube. The verified renderer currently produces
  1080x1920 only.
- F9 resume/error workflow/retention cleanup.
- A reliable user-facing dashboard connected to this pipeline.

Live-state observations during this audit:

- Docker Desktop was not running, so container health and the active imported
  workflow could not be verified.
- The checked-in F7 workflow has `active: false`; this does not prove the copy in
  n8n's database is inactive, only that the exported JSON is.
- `pipeline.db` contains two rendered videos and one video at `chunked`.
- It contains one `render/running` job. Because Docker is stopped, this is likely
  an orphan that should be reaped and surfaced at the next service start.
- All rendered chunk rows still have `ready_to_upload = 0`, consistent with F8
  not being implemented.
- The migrated automation unit includes the user-owned callback changes in
  `automation/shared/callbacks.py` and `automation/shared/test_callbacks.py`.
  The migration preserved their contents.

### 3.2 Scheduler app audit

The app is a small Next.js 16 + Prisma/PostgreSQL scheduler. It has a local
YouTube uploader and an optional MuAPI TikTok path. Facebook is UI-only
"coming soon."

Useful pieces to retain:

- Next.js dashboard shell and integrations page.
- PostgreSQL/Prisma.
- Google OAuth and resumable YouTube upload start.
- Basic post history and local file preview.

Structural problems to fix before adding more platforms:

1. **A read request publishes content.** `GET /api/posts` finds due posts,
   uploads them, and polls external jobs. Publishing only happens while a page
   is polling, and concurrent reads can double-submit a post.
2. **There is no durable worker or lease.** A web server restart can strand work;
   no worker owns a job, heartbeat, attempt number, or retry time.
3. **The account model is not a social-account model.** The Prisma `Account`
   table is NextAuth storage. The UI invents YouTube account ID `1`, while
   `ScheduledPost.accountId` is an integer. This cannot safely represent 5-10
   Google, TikTok, and Facebook identities.
4. **Local mode chooses the first Google account.** It does not select the
   requested YouTube channel and cannot reliably manage many accounts.
5. **Tokens are plaintext OAuth fields.** There is no app-level token
   encryption, credential health, rotation, reconnect state, or audit trail.
6. **The upload endpoint buffers the entire file.** The YouTube publisher also
   reads the entire video into memory. Large videos should be streamed.
7. **No shared media contract exists between projects.** The app uses
   `public/uploads`; n8n uses `/data/<video_id>/...`.
8. **A post mixes campaign, target, and attempt.** One source going to 30
   accounts needs one campaign, many platform targets, and multiple attempts per
   target—not 30 unrelated `ScheduledPost` rows.
9. There is no asset-variant model, approval model, affiliate-product snapshot,
   post-publish action, idempotency key, outbox, or observability timeline.
10. YouTube upload supports title/description/tags/privacy but does not yet call
    `thumbnails.set`.

## 4. Desired domain model

Use these concepts in PostgreSQL. Names may change; the separation should not.

| Concept | Meaning | Important fields |
|---|---|---|
| `SourceVideo` | One ingested source and its production run | n8n video ID, title, rights status, render revision |
| `ContentItem` | One derived delivery unit | source video ID, `WHOLE_VIDEO | CHUNK`, nullable chunk index, duration |
| `AssetVariant` | A file made for a delivery format | kind, ratio, width, height, duration, path, checksum, ready state |
| `Campaign` | One approved distribution decision for a source video | source video, schedule, approval state, selected product |
| `PublishTarget` | One campaign x content item x social account | platform, account, metadata snapshot, state, idempotency key |
| `PublishAttempt` | One external API attempt | attempt number, request/response summary, external ID, error class |
| `PostAction` | Work after the main post | Facebook text/photo comment, status, dependency on external post ID |
| `SocialAccount` | One YouTube channel, TikTok creator, or Facebook Page | provider ID, label, scopes, token envelope, health |
| `ApprovalRequest` | One review decision | revision, Telegram chat/message, token hash, expiry, decision |
| `AffiliateProduct` | Cached Shopee offer | item/shop IDs, commission snapshot, sales, rating, image, expiry |
| `OutboxEvent` | Durable notification/integration event | type, aggregate ID, payload, processed time |
| `AuditEvent` | Human-readable append-only timeline | actor, action, old/new state, trace ID, timestamp |

Recommended campaign states:

`draft -> rendering -> ready_for_review -> approved | rejected | expired -> queued -> publishing -> published | partial_success | failed | cancelled`

Recommended target states:

`queued -> claimed -> uploading -> platform_processing -> published -> post_actions -> complete`

Any stage may enter `retry_wait`, `failed`, or `dead`. State transitions must be
validated; clients must not set arbitrary status strings.

## 5. Platform delivery contract

Treat platform metadata as a versioned snapshot on each `PublishTarget`. Editing
a campaign after approval creates a new revision and invalidates the old
approval.

| Platform | Video asset | Main payload | Follow-up work |
|---|---|---|---|
| YouTube | **One whole video**, `LANDSCAPE_16_9`, recommended 1920x1080 | title, description, tags, category, privacy, made-for-kids | custom thumbnail via `thumbnails.set` |
| TikTok | **One draft per chunk**, `VERTICAL_9_16`, recommended 1080x1920 | the video Upload endpoint accepts media-transfer fields, not the final video caption/hashtags | export draft; show/send prepared caption and hashtags for the operator to apply while finishing in TikTok |
| Facebook Page | **One post per chunk**, `VERTICAL_9_16` Reel when eligible; otherwise Page video | title/description/caption | child text/photo comment containing thanks text, QA/donation image, and Shopee affiliate link |

This mapping is a hard invariant, not a UI default. For a source with three
chunks and one account on each platform, approval creates **seven targets**:
one YouTube whole-video target, three Facebook chunk targets, and three TikTok
chunk targets. With multiple accounts, the formula is:

`YouTube accounts + (chunk count x Facebook accounts) + (chunk count x TikTok accounts)`

Facebook comments must be separate dependent jobs because a comment needs the
published Facebook object ID. A failed comment should yield `partial_success`,
not re-upload the successful video.

Official APIs support the core operations:

- [YouTube videos and custom thumbnails](https://developers.google.com/youtube/v3/docs)
- [Meta's official Facebook API collection: Reels Publishing](https://www.postman.com/meta/facebook/documentation/r56bjfd/facebook-api)
- [Facebook object comments (`message`, `attachment_url`, or multipart `source`)](https://developers.facebook.com/docs/graph-api/reference/object/comments/)
- [Telegram Bot API inline keyboards and callback queries](https://core.telegram.org/bots/api)

Do not hard-code platform duration limits. TikTok explicitly returns
`max_video_post_duration_sec` from `creator_info`; validate at approval time and
again immediately before upload. Facebook Reel eligibility should also be
queried/validated against the API version in use, with Page video as an explicit
fallback—not a silent change.

For the selected TikTok **draft** MVP, use the Upload Video endpoint and
`video.upload` scope, not Direct Post and `video.publish`. The current official
Upload API accepts `source_info` for the video but no final video caption,
hashtags, or cover selection. The operator completes those in TikTok's editing
flow. The app should put the prepared caption/hashtags beside each draft and in
the Telegram notification for easy copy/paste. TikTok currently documents a
maximum of five pending shares per account within 24 hours and a ten-minute
maximum video for this upload endpoint, so three chunks from one source consume
three of those five pending slots.

- [TikTok Upload Video API](https://developers.tiktok.com/docs/en/content-posting-api-reference-upload-video)
- [TikTok draft-upload scope](https://developers.tiktok.com/docs/en/tiktok-api-scopes)

## 6. Three methods for each feature

`A` is the recommended method unless stated otherwise.

### 6.1 n8n -> app handoff

- **A — Signed render-complete webhook:** n8n calls the app with `video_id`,
  chunk IDs, paths, checksums, warnings, and a timestamp/HMAC. The app upserts by
  deterministic key and writes an outbox event. Best separation and easiest to
  test.
- **B — App polls `pipeline.db`:** simple, but couples the app to SQLite schema,
  delays updates, and makes exact once-only handoff harder.
- **C — Both systems share one database:** fewer hops, but n8n media services and
  the app become tightly coupled and migration ownership becomes unclear.

### 6.2 Media storage

- **A — Shared bind volume, publisher read-only:** best for this local machine;
  no duplicate multi-gigabyte copy. Store path plus SHA-256 and probe metadata.
- **B — Self-hosted S3/MinIO:** best when app/workers move to other machines or
  TikTok/Meta must pull from a public URL.
- **C — Copy into `public/uploads`:** easy but duplicates files, exposes them,
  and makes retention inconsistent. Do not use for production.

### 6.3 Dual aspect ratios

- **A — Render one landscape whole-video asset and vertical per-chunk assets:**
  `yt-landscape/final.mp4` plus `chunks/<idx>/vertical.mp4`; deterministic,
  highest quality, and exactly matches the chosen publishing units.
- **B — Render one landscape master, then crop/pad to vertical:** less render
  work but may hide important content and needs per-source safe zones.
- **C — One vertical asset everywhere:** simplest, but conflicts with the stated
  YouTube 16:9 requirement.

### 6.4 Durable publish queue

- **A — PostgreSQL queue with `FOR UPDATE SKIP LOCKED`, leases, and a worker:**
  no new infrastructure, durable, enough for the expected scale.
- **B — BullMQ + Redis:** mature delayed jobs/retries and good if concurrency
  grows; adds another stateful service.
- **C — n8n executes every platform upload:** visually convenient, but OAuth,
  retries, idempotency, and per-target state become difficult to keep coherent.

### 6.5 Approval

- **A — Telegram button calls app approval endpoint:** one-use token, revision
  check, chat allowlist, expiry, and atomic state transition. For the MVP, one
  approval covers the complete campaign fan-out across all selected platforms
  and accounts. Dashboard uses the same endpoint.
- **B — n8n Wait node with Telegram callback:** quick prototype; long-running
  approval executions are harder to inspect and migrate.
- **C — Dashboard-only approval:** safest and simplest technically, but loses
  the requested mobile notification/button convenience.

Telegram `callback_data` is small. Put an opaque random ID in the button, never
the full publish payload or a reusable signed command. Always answer the callback
query promptly, then edit the message to show the decision.

### 6.6 Multi-account credentials

- **A — Official OAuth per platform, one encrypted `SocialAccount` row per
  channel/Page/creator:** best control and observability. Encrypt refresh/access
  tokens with AES-256-GCM or envelope encryption; never expose them to the UI or
  n8n expressions.
- **B — Self-hosted Postiz/TryPost as a publishing sidecar:** broad provider code
  already exists; adds another large application and database, and its license/
  operational fit must be reviewed.
- **C — Browser profiles/cookie automation:** can bypass API onboarding but is
  fragile, difficult to secure, and may violate platform terms. Not recommended.

### 6.7 YouTube publishing

- **A — Extend the current direct YouTube adapter:** stream a resumable upload,
  then set the thumbnail, poll processing, and store the video ID. Reuse the
  existing Google work only after decoupling it from NextAuth users.
- **B — Delegate to Postiz/TryPost:** faster provider coverage but heavier stack.
- **C — Continue MuAPI:** quickest, but contradicts the local/no-SaaS goal and
  duplicates the direct uploader already present.

### 6.8 Facebook publishing and first comment

- **A — Official Page Graph API adapter:** publish Reel/Page video, wait for the
  object ID, then enqueue `COMMENT_TEXT` and optionally `COMMENT_PHOTO` actions.
- **B — Publishing sidecar:** let Postiz/TryPost publish, then call the Graph API
  directly for the specialized photo/comment step.
- **C — Browser automation:** only as a consciously accepted last resort; it is
  harder to make safe or idempotent.

### 6.9 TikTok publishing

- **A — Upload API to TikTok drafts/manual finish:** most compatible with the
  current private, human-approved use case, but not fully automatic. For video,
  caption, hashtags, privacy, and cover are completed manually in TikTok.
- **B — Keep MuAPI for TikTok only:** potentially automatic if MuAPI's approved
  product and terms cover these accounts; verify costs, limits, data handling,
  and whether 5-10 accounts are allowed.
- **C — Official Direct Post:** technically clean, but only proceed after TikTok
  approves the product/use case. An unaudited client is private-only, and the
  current internal re-upload scenario conflicts with published audit guidance.

### 6.10 Shopee top-products page

- **A — Official Shopee Affiliate Open API:** cache `productOfferV2` results,
  request top-performing offers and/or sort by commission, and generate links
  with `generateShortLink`. This requires an approved Affiliate account,
  `app_id`, and `secret_key`.
- **B — Third-party product-data service:** easy prototype and helpful fallback,
  but introduces trust, uptime, freshness, and affiliate-attribution risk.
- **C — Scrape Shopee pages/private endpoints:** fragile under anti-bot changes
  and risky for terms/compliance. Do not make it the primary source.

The page should not rank only by commission rate. Default score should combine:

`expected value = estimated commission amount x demand signal x quality signal x offer availability`

Show sortable raw fields too: commission amount/rate, sales, rating, price,
discount, remaining budget, seller type, and offer expiry. Snapshot the chosen
product/link onto the campaign so a later API refresh cannot silently change an
already-approved post.

Community reference derived from Shopee's official Affiliate portal:

- [Shopee Affiliate API notes and GraphQL examples](https://github.com/bcat95/shopee-aff)
- [Typed Shopee Affiliate SDK example](https://github.com/gregojoao/shopee-affiliate)

### 6.11 Metadata, thumbnails, and templates

- **A — Versioned per-platform templates + optional LLM draft:** deterministic
  limits/disclosures, editable before approval, and reproducible later.
- **B — LLM-only generation:** flexible but harder to reproduce and validate.
- **C — One caption copied to every platform:** cheapest but ignores platform
  fields and the requested Facebook-only comment/affiliate behavior.

### 6.12 Visual management interface

- **A — Extend this Next.js app:** add Inbox, Campaigns, Calendar/Queue,
  Products, Accounts, and Runs. It preserves the local workflow and existing UI.
- **B — Adopt a full scheduler such as Postiz/TryPost:** fastest route to a
  mature calendar and provider ecosystem, but migration and customization are
  significant.
- **C — Use n8n UI + Google Sheets:** useful for debugging, not a safe operating
  console for many accounts and partial failures.

Useful open-source patterns to study, not copy blindly:

- [Postiz](https://github.com/gitroomhq/postiz-app): provider-specific settings,
  media upload layer, many integrations.
- [TryPost](https://github.com/trypostit/trypost): official APIs, multi-account
  switch, approval/workspace concepts.
- [Social Poster](https://github.com/EbaAdisu/social-media-poster): separate web,
  API, worker, PostgreSQL, Redis, MinIO, and encrypted token storage.
- [Open Dispatch](https://github.com/Matthew-Selvam/Open-Dispatch): small adapter
  contract and explicit queue state machine.
- [n8n Telegram approval example](https://n8n.io/workflows/9363-one-telegram-chat-to-edit-thumbnail-and-auto-post-your-videos-everywhere/):
  inline approval interaction and per-platform result reporting.

## 7. Design patterns to use

### Hexagonal architecture / provider adapters

Core publishing logic depends on a small interface, not on Facebook/TikTok/
YouTube response shapes:

```text
PublisherAdapter
  validate(target, asset)
  refreshCredential(account)
  publish(target, asset, idempotencyKey)
  poll(externalJobId)
  performPostAction(action, externalPostId)
```

Each adapter translates common concepts into the provider's exact API. Keep
platform-specific fields in validated JSON plus a schema version; do not force
all providers into one giant nullable table.

### Saga / process manager

A campaign fan-outs into independent targets. YouTube may succeed while
Facebook's comment fails and TikTok is blocked. Do not roll back by deleting
successful posts. Record partial success, retry only the failed target/action,
and let the operator explicitly request deletion where APIs support it.

### Transactional outbox

In one database transaction, change state and insert the event that should wake
the worker/send Telegram. A dispatcher later delivers outbox events. This avoids
"database says ready but notification was lost" and "Telegram sent but queue row
was never committed."

### Idempotent consumer

Use stable keys such as:

`publish:<campaign_revision>:<social_account_id>:<platform>`

and for Facebook:

`comment:<publish_target_id>:<action_index>`

Store external request/job IDs before polling. A timeout after the provider
accepts a video is an **unknown outcome**, not permission to upload it again.

### Lease + retry policy

Workers claim a row with `locked_by`, `locked_until`, and heartbeat. Classify
errors:

- transient: network, 429, selected 5xx -> exponential backoff + jitter;
- credential: refresh once, then `reconnect_required`;
- validation/policy: no automatic retry;
- unknown outcome: reconcile by external job/request ID before retry;
- exhausted: `dead`, alert Telegram, keep a manual retry action.

### Bulkhead and rate limiter

Limit concurrency per platform and per account. One Facebook token failure must
not block YouTube. Respect `Retry-After` and platform-provided quotas rather than
using one global delay.

## 8. Most important backend concepts

1. **Exactly-once intent, at-least-once execution.** Networks and processes fail.
   Design retries to be safe; do not claim literal exactly-once delivery.
2. **Unknown outcomes.** A client timeout may occur after the platform accepted
   the upload. Reconcile before resubmission.
3. **Approval binds to a revision.** Any change to asset, caption, account,
   product, comment, or schedule invalidates approval.
4. **Credential isolation.** Encrypt tokens, keep scopes minimal, redact logs,
   record expiry/health, and provide reconnect/revoke flows.
5. **Media integrity.** Record SHA-256, codec, resolution, duration, size, and
   generated-at for every variant. Validate before approval and before upload.
6. **Partial success is normal.** Campaign status is derived from target/action
   states, not a single boolean.
7. **Backpressure.** Do not render or approve hundreds of posts faster than
   platform/account limits permit. Show earliest retry/eligible time.
8. **Observability.** Every campaign gets a trace ID and append-only timeline;
   external request IDs are searchable. Metrics should cover queue age, success
   rate, retry count, token health, and publish latency by provider.
9. **Retention.** Keep published artifacts according to a policy; never delete
   a file while a queued target still references it. Use reference counts or an
   eligibility query.
10. **SSRF and path safety.** Allowlisted source hosts, canonicalize shared-volume
    paths, reject traversal, cap file sizes, and never let request JSON select an
    arbitrary local path.
11. **Affiliate disclosure and attribution.** Preserve sub-IDs per campaign/
    account, disclose commercial relationships where required, and never replace
    an approved affiliate link silently.
12. **Auditability.** Store who approved, what exact revision was approved, when
    each provider call occurred, and what changed on retry.

## 9. Prioritized backlog

Each task is intended to be independently reviewable and normally fit within
roughly half a day to two focused days. Estimates are directional, not promises.

### P0 — Decisions and feasibility gates

- [ ] **P0.1 Rights gate:** add `rights_status` and require
  `owned | licensed | public_domain | permission` before approval.
  - Accept: an unknown-rights item cannot reach `approved`.
- [x] **P0.2 Confirm upload unit:** YouTube publishes one whole 16:9 video;
  Facebook and TikTok publish each 9:16 chunk as a separate post.
  - Accept: for N chunks and one account/platform, the campaign contains
    `1 + N + N` publish targets and each target references the correct asset.
- [ ] **P0.3 Platform sandbox spike:** obtain/test one YouTube channel, one
  administered Facebook Page, and TikTok Upload-to-Drafts.
  - Accept: permissions/scopes, test visibility, upload limits, and external IDs
    are recorded without building the full UI.
- [ ] **P0.4 Shopee credential spike:** verify that this Affiliate account has
  Open API access and run `productOfferV2` plus `generateShortLink`.
  - Accept: one real offer and tracked short link are returned.
- [ ] **P0.5 Output specification:** define landscape/vertical duration,
  resolution, safe zones, thumbnail rules, and Facebook Page-video fallback.

### P1 — Reliable single-account vertical slice

- [ ] **P1.1 Replace `ScheduledPost` with the new core schema** using additive
  Prisma migrations; keep old rows readable during transition.
- [ ] **P1.2 Add signed idempotent `/api/internal/render-complete`.**
- [ ] **P1.3 Add the review Inbox page** showing both previews, warnings,
  metadata, selected accounts, and revision.
- [ ] **P1.4 Add Telegram outbox delivery** with Approve/Reject/Open buttons.
- [ ] **P1.5 Add atomic approval endpoint** with one-use token, expiry, chat
  allowlist, revision comparison, and one all-platform campaign decision.
- [ ] **P1.6 Create standalone publisher worker** and PostgreSQL lease logic.
- [ ] **P1.7 Refactor YouTube into an adapter** with streamed resumable upload,
  thumbnail, processing poll, idempotency/reconciliation, and attempt history.
- [ ] **P1.8 End-to-end test** from mocked F7 callback to a private YouTube video.
  - Accept: refresh/restart during queueing does not duplicate the upload.

### P2 — Media variants and Facebook

- [ ] **P2.1 Add `yt-landscape` render preset/output** and write both variants to
  the manifest with checksums/probe data.
- [ ] **P2.2 Add preflight validators** for each account/platform immediately
  before approval and upload.
- [ ] **P2.3 Add Facebook OAuth/Page discovery** and encrypted Page tokens.
- [ ] **P2.4 Add Facebook Reel/Page-video adapter** with processing poll.
- [ ] **P2.5 Add text/photo comment actions** and partial-success UI.
- [ ] **P2.6 Test one campaign where video succeeds and comment fails.**
  - Accept: retrying the comment never re-uploads the video.

### P3 — Shopee affiliate workflow

- [ ] **P3.1 Implement signed Shopee GraphQL client** with secret redaction,
  timeout, retry, and response validation.
- [ ] **P3.2 Add daily/hourly offer sync** with expiry and last-known-good cache.
- [ ] **P3.3 Build Products page** with Top 50, filters, score explanation,
  freshness, and selectable product.
- [ ] **P3.4 Generate short links on campaign revision** with traceable sub-IDs.
- [ ] **P3.5 Insert the immutable link snapshot into Facebook comment preview.**
- [ ] **P3.6 Add conversion import/reporting later**; it is not required to ship
  selection and publishing.

### P4 — TikTok feasibility-dependent delivery

- [x] **P4.1 Choose route:** official TikTok Upload-to-Drafts for the MVP;
  finishing/publication remains a human action in the TikTok app.
- [ ] **P4.2 Add TikTok OAuth/account health** only for the chosen route.
- [ ] **P4.3 Add draft preflight** for the documented ten-minute maximum,
  supported codecs/file size, token scope, and five-pending-shares-per-24h cap.
- [ ] **P4.4 Upload/poll adapter** using `video.upload`; keep prepared caption
  and hashtags in the app/Telegram for manual use and do not imply the video API
  prefilled them or selected a cover.
- [ ] **P4.5 Private/draft end-to-end test** before any public account.

### P5 — 5-10 accounts per platform and operations

- [ ] **P5.1 Account matrix UI:** labels, provider identity, scopes, expiry,
  health, last success/failure, reconnect/revoke.
- [ ] **P5.2 Fan-out target selector** with per-account metadata override.
- [ ] **P5.3 Per-account/platform concurrency and rate limits.**
- [ ] **P5.4 Retry/dead-letter/reconcile controls** with audit entries.
- [ ] **P5.5 Calendar/queue/runs views** and platform/account filters.
- [ ] **P5.6 F9 retention sweep** made reference-safe for queued targets.
- [ ] **P5.7 Backup/restore drill** for PostgreSQL, encrypted credentials, and
  n8n data; document key recovery separately from database backup.
- [ ] **P5.8 Failure drills:** kill worker mid-upload, expire a token, return 429,
  lose callback, duplicate callback, and fail a Facebook comment.

## 10. Recommended end-to-end pipeline

1. User submits a licensed source URL and selects a source-channel render preset.
2. n8n performs the existing ingest/transcribe/translate/chunk/voice stages.
3. The render service creates one whole-video `LANDSCAPE_16_9` asset for
   YouTube and one `VERTICAL_9_16` asset for every chunk used by Facebook and
   TikTok, plus manifest/checksums/probe metadata.
4. n8n calls the app's signed render-complete webhook. The app idempotently
   upserts content/assets, generates platform drafts, and inserts an outbox event.
5. The outbox dispatcher sends Telegram a compact preview and one-use buttons.
6. Approval atomically verifies chat, token, expiry, rights status, and revision,
   then fans out targets by the fixed mapping: one whole-video target per
   selected YouTube account and one target per chunk for every selected Facebook
   and TikTok account.
7. The worker claims eligible targets. It validates credential health, asset
   limits, and platform metadata before contacting the provider.
8. YouTube whole-video publishing, Facebook chunk publishing, and TikTok chunk
   export-to-drafts execute independently under provider/account concurrency
   limits.
9. After Facebook returns a stable object ID, child comment actions add the
   thank-you text, optional QA/donation image, and immutable Shopee link snapshot.
10. Provider polling/reconciliation finishes target states. Campaign status is
    derived as published, partial success, or failed.
11. Every transition appends an audit event and updates the dashboard. Terminal
    success/failure sends a final Telegram summary with platform URLs and retry
    actions where appropriate.
12. Retention removes raw sources only after the configured age and only when no
    queued/active target references them.

## 11. Confirmed decisions and remaining questions

Confirmed:

- YouTube receives the whole 16:9 video.
- Facebook Pages receive separate 9:16 chunk posts.
- TikTok receives separate 9:16 chunks as drafts in the MVP.
- Facebook destinations are Pages you administer.
- One Telegram approval temporarily authorizes the entire campaign fan-out
  across every selected platform and account.

Still to decide:

1. Does your Shopee Affiliate account already show Open API credentials
   (`app_id` and `secret_key`)? Without them, the Top 50 page needs a temporary
   third-party/manual-import mode.
2. Should approval select all connected accounts by default, or a saved account
   group such as `Mystery VN` / `Product Reviews`?

## 12. Definition of the first useful release

The first release is complete when one rendered source appears in the web Inbox,
Telegram approval is accepted exactly once, one selected YouTube channel
receives the **whole** 16:9 video and thumbnail, the dashboard shows a searchable
attempt timeline and external URL, and restarting the web app/worker at any
point does not lose or duplicate the job.

That release deliberately excludes Facebook, TikTok, Shopee ranking, and
multi-account fan-out. It proves the architecture with the lowest-risk official
provider before multiplying the failure surface.
