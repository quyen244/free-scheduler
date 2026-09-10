# Automated re-up workflow and pipeline lifecycle

Status: approved concept prototype  
Last updated: 2026-09-09  
Companion prototype: [reup-pipeline-lifecycle-prototype.html](reup-pipeline-lifecycle-prototype.html)

## Outcome

Show how one YouTube URL moves through media production, review, one Telegram
approval, platform delivery, retries, user recovery, and the final notification.
This is the target behavioral contract, not evidence that production already
implements every stage.

## Scope

Included:

- YouTube URL submission, media validation, and deduplication.
- Whole 16:9 YouTube output and variable 4-5 minute 9:16 chunks.
- Whole-video and per-chunk OpenAI metadata generation.
- Brand profiles containing platform accounts, watermark, and signature music.
- Review, editing, revision invalidation, and one Telegram approval.
- Independent platform targets, retry attempts, partial publishing failure, and
  final summary notifications.
- Persistent state, database relationships, retention, and recovery actions.

Not included:

- Live platform calls, credentials, or automatic TikTok public posting.
- Final OpenAI model, prompt wording, music volume, or real brand assets.
- Local-file upload/import, which is deferred until URL ingestion is reliable.
- Production implementation or deployment.

## Confirmed behavior

- YouTube receives the complete branded 16:9 video.
- The YouTube whole output preserves original visuals while replacing audio
  with the edited Vietnamese voice and adding subtitles, watermark, and quiet
  signature music.
- Facebook and TikTok receive unique, ordered, branded 9:16 chunks.
- Sources from 5-9 minutes create one chunk; longer sources create balanced
  4-5 minute chunks with boundaries shifted up to 15 seconds to end speech
  cleanly.
- One brand profile groups its YouTube, Facebook, and TikTok accounts and uses
  the same watermark and signature music across both ratios.
- One approval covers one immutable campaign revision.
- Every platform metadata object contains five hashtags: three
  content-specific and two relevant broad/discovery tags.
- Metadata is Vietnamese by default and uses curiosity-driven, engaging copy
  without misleading hooks or unsupported claims. Each platform metadata object
  contains at most two relevant emojis.
- The OpenAI model is selected cost-first against fixed acceptance fixtures; the
  exact production model and cost ceiling are recorded after that evaluation.
- The metadata service reads `OPENAI_API_KEY` only from its server-side
  container environment.
- Processing failures before approval block review or approval.
- After approval, already successful uploads remain successful; only failed
  targets retry. The campaign is partial_failure until required targets reach
  their accepted outcomes.
- TikTok success means draft_delivered, not publicly published.
- Every failure is stored. Telegram waits until automatic retries are exhausted
  before asking the operator to act, then sends a final summary.

## System relationship

~~~mermaid
flowchart LR
    U[Operator] --> A[Next.js review app]
    U --> TG[Telegram]
    A --> N[n8n orchestration]
    N --> MS[Media services]
    N --> AI[Metadata service]
    MS --> FS[(Local media storage)]
    AI --> OAI[OpenAI API]
    N --> SQ[(n8n SQLite state)]
    N --> H[Signed app handoff]
    H --> PG[(Application PostgreSQL)]
    PG --> W[Publisher worker]
    W --> Y[YouTube]
    W --> F[Facebook Pages]
    W --> T[TikTok inbox drafts]
    PG --> TG
~~~

Ownership boundary:

- n8n coordinates asynchronous stages and callbacks.
- Docker services own testable download, transcript, translation, metadata,
  voice, render, and media-validation rules.
- Local storage owns large files; databases store paths, hashes, states, and
  history rather than video bytes.
- The application owns revisions, review, approval, targets, and user recovery.
- The publisher worker owns official platform API calls and target attempts.

## Complete processing and publishing flow

~~~mermaid
flowchart TD
    S[Submit YouTube URL] --> V[Validate media, duration,<br/>resolution, identity, and duplicate state]
    V -->|Invalid| NA[needs_action: explain problem]
    NA -->|Correct and resume| V
    V -->|Valid| I[Ingest or stage source]
    I --> TR[Transcribe]
    TR --> TL[Translate when needed]
    TL --> CH[Create balanced, sentence-safe chunks]
    CH --> MD[Generate whole and per-chunk metadata]
    MD --> VO[Generate voice]
    VO --> CM[Render clean 16:9 and 9:16 masters]
    CM --> BV[Apply each brand's watermark and signature music]
    BV --> MF[Probe outputs and build media manifest]
    MF -->|Any required asset invalid| PF[Retry failed production stage]
    PF -->|Exhausted| NA
    MF -->|Complete| RV[Create immutable review revision]
    RV --> ED{Operator edits?}
    ED -->|Post text only| NR[New revision; no rerender]
    ED -->|Visual, music, or branding| RR[New revision; rerender affected variants]
    NR --> RV
    RR --> MF
    ED -->|No| AR[Send Telegram approval request]
    AR -->|Reject| RJ[Revision rejected]
    AR -->|Approve all| Q[Create all publish targets atomically]
    Q --> PY[YouTube target workers]
    Q --> PFb[Facebook target workers]
    Q --> PT[TikTok draft workers]
    PY --> AG[Aggregate target outcomes]
    PFb --> AG
    PT --> AG
    AG -->|All accepted outcomes| DONE[Campaign completed]
    AG -->|Some failed after retries| PART[partial_failure + operator alert]
    PART -->|Retry failed targets only| AG
    DONE --> SUM[Final Telegram and app summary]
~~~

## Campaign state lifecycle

~~~mermaid
stateDiagram-v2
    [*] --> submitted
    submitted --> validating
    validating --> processing: source accepted
    validating --> needs_action: invalid or missing data
    processing --> retry_wait: transient stage failure
    retry_wait --> processing: automatic retry
    processing --> needs_action: retries exhausted
    needs_action --> processing: retry failed stage
    needs_action --> failed_terminal: operator stops or source unavailable
    processing --> ready_for_review: manifest valid
    ready_for_review --> approval_pending
    approval_pending --> rejected
    approval_pending --> approved
    ready_for_review --> processing: visual edit invalidates render
    ready_for_review --> ready_for_review: metadata edit creates revision
    approved --> publishing
    publishing --> partial_failure: some targets fail
    partial_failure --> publishing: retry failed targets
    publishing --> completed: all accepted outcomes reached
    rejected --> [*]
    failed_terminal --> [*]
    completed --> [*]
~~~

needs_action and partial_failure are recoverable. failed_terminal means the
operator stopped the campaign or the source can no longer be obtained; it does
not blacklist the source URL forever.

## Target state lifecycle

~~~mermaid
stateDiagram-v2
    [*] --> pending_approval
    pending_approval --> queued: campaign approved
    queued --> leased
    leased --> uploading
    uploading --> retry_wait: timeout, 429, or 5xx
    retry_wait --> queued: backoff elapsed
    uploading --> needs_action: retries exhausted or credential/config failure
    needs_action --> queued: operator retries
    uploading --> published: YouTube or Facebook accepted
    uploading --> draft_delivered: TikTok inbox accepted
    published --> [*]
    draft_delivered --> [*]
~~~

## Data relationships

~~~mermaid
erDiagram
    SOURCE_VIDEO ||--o{ SOURCE_SUBMISSION : identified_by
    SOURCE_VIDEO ||--o{ CAMPAIGN : reused_by
    SOURCE_VIDEO ||--o{ CONTENT_ITEM : derives
    CAMPAIGN ||--o{ CAMPAIGN_REVISION : versions
    CAMPAIGN ||--o{ PROCESSING_ATTEMPT : runs
    BRAND_PROFILE ||--o{ BRAND_ACCOUNT : groups
    SOCIAL_ACCOUNT ||--o{ BRAND_ACCOUNT : assigned_to
    BRAND_PROFILE ||--o{ BRAND_MEDIA_ASSET : owns
    CAMPAIGN_REVISION }o--o{ BRAND_PROFILE : selects
    CONTENT_ITEM ||--o{ METADATA_REVISION : describes
    CONTENT_ITEM ||--o{ ASSET_VARIANT : renders
    BRAND_PROFILE ||--o{ ASSET_VARIANT : brands
    CAMPAIGN_REVISION ||--o{ PUBLISH_TARGET : plans
    PUBLISH_TARGET ||--o{ PUBLISH_ATTEMPT : tries
    PUBLISH_TARGET ||--o{ POST_ACTION : follows
    CAMPAIGN_REVISION ||--o{ APPROVAL_REQUEST : authorizes
    CAMPAIGN ||--o{ AUDIT_EVENT : records
    CAMPAIGN ||--o{ OUTBOX_EVENT : notifies
~~~

Important identities:

- SourceVideo: stable identity using normalized YouTube ID and downloaded
  SHA-256.
- Campaign: one intended distribution of a source. Reprocessing a published
  source creates a new campaign rather than overwriting history.
- CampaignRevision: immutable review snapshot. Material edits create a new
  revision and invalidate previous approval.
- ProcessingAttempt: append-only attempt for ingest, metadata, render, or
  validation. Successful earlier stages may be reused.
- PublishTarget: one content item x one social account x one campaign revision.
- PublishAttempt: append-only API attempt. Retrying one target does not recreate
  successful peer targets.

## Current storage versus target storage

The current automation database already has videos, chunks, and jobs. Those
tables support the existing ingest-through-render workflow but do not yet fully
represent brand profiles, metadata revisions, approvals, platform targets, or
attempt history.

~~~mermaid
flowchart LR
    CS[(Current SQLite<br/>videos, chunks, jobs)] --> N[n8n processing]
    N --> M[Versioned media manifest]
    M --> PG[(Target PostgreSQL<br/>campaigns, revisions, targets,<br/>attempts, approvals, events)]
    PG --> UI[Review and recovery UI]
    PG --> WK[Publisher worker]
~~~

The manifest and signed handoff form the boundary: SQLite can remain optimized
for local media jobs while PostgreSQL becomes authoritative for review and
publishing state.

## Retry and recovery rules

| Failure class | Examples | Automatic behavior | User support |
|---|---|---|---|
| Transient | network timeout, HTTP 429, provider 5xx | retry up to three times with backoff | show attempt count; alert only after exhaustion |
| Invalid input | corrupt file, below 720p, outside 5-20 minutes | do not retry | explain the exact rule and allow replacement |
| Missing configuration | music missing or expired credential | do not retry | show the missing item and direct fix/reconnect action |
| Derived output invalid | corrupt render, wrong ratio, missing audio | retry affected render once, then pause | inspect validation result and retry failed stage |
| Stale revision | metadata, brand, target, or visual edit after approval | invalidate approval | prepare new revision and send approval again |
| Partial publish | one target fails after peers succeed | keep successes; retry failed target only | show partial result and safe retry action |
| Unknown provider outcome | timeout after upload may have succeeded | reconcile using stored provider/session ID | do not blindly upload again |

## Telegram and application notifications

~~~mermaid
sequenceDiagram
    participant N as n8n/worker
    participant DB as Database/outbox
    participant TG as Telegram
    participant U as Operator
    N->>DB: record every attempt and failure
    alt automatic retries remain
        N->>N: backoff and retry silently
    else action required
        DB->>TG: failure reason + Retry/Open buttons
        U->>TG: Retry failed stage
        TG->>DB: consume action once
        DB->>N: resume from failed boundary
    end
    DB->>TG: approval request for exact revision
    U->>TG: Approve all
    TG->>DB: create targets atomically
    N->>DB: platform outcomes
    DB->>TG: final or partial summary
~~~

The user should always see what failed, what already succeeded, whether an
automatic retry remains, the next safe action, whether work resumes or restarts,
and the final accepted outcome including draft_delivered for TikTok.

## Retention lifecycle

~~~mermaid
flowchart TD
    A[Campaign artifacts] --> S{Campaign result}
    S -->|Completed| C1[Delete temporary files after 1 day]
    S -->|Completed| C2[Delete source/final files after 7 days]
    S -->|Failed/needs action| F1[Keep files for 3 days after last failure]
    F1 --> F2{Retried before expiry?}
    F2 -->|Yes| R[Reuse valid artifacts and resume]
    F2 -->|No| D[Delete files; retain metadata/audit]
    D --> NEW[Later retry redownloads source<br/>under a new attempt/campaign]
    A --> K[Keep database metadata and audit<br/>until manual deletion]
~~~

Never delete an asset referenced by active, approved, queued, leased,
publishing, or unknown-outcome work.

## Prototype scenarios

1. **Complete success** - one brand, three chunks, one approval, seven accepted
   outcomes, and a final Telegram summary.
2. **Automatic retry succeeds** - OpenAI metadata receives HTTP 429, backs off,
   retries only the affected generation, then continues.
3. **Failure before approval** - a vertical render fails media validation, the
   campaign pauses, the operator retries the failed stage, and review resumes.
4. **Multi-brand partial publishing failure** - two brands create separate
   branded variants and targets; one TikTok draft fails after peer targets
   succeed, then only that target retries.

All names, IDs, durations, counts, timings, and platform results in the prototype
are deterministic mock/demo data.

## Acceptance criteria for the prototype

- The user can select and run all four scenarios deterministically.
- Queued, active, completed, retrying, needs-action, partial-failure, rejected,
  and completed states are visibly distinct.
- The run pauses at approval and exposes Approve all, Reject, and review context.
- The failure-before-approval scenario exposes Retry failed stage and resumes
  without repeating successful work.
- The partial-publish scenario preserves successful targets and retries only the
  failed TikTok target.
- Selecting a stage explains it in plain language and optionally reveals its
  mock database writes and attempt history.
- Telegram approval, action-required, partial, and final-summary messages appear
  at the correct lifecycle points.
- Start, pause/resume, step, reset, and speed controls work on narrow and desktop
  layouts.

## Open implementation decisions

These do not block the concept prototype but must be answered before production:

- Exact OpenAI model and per-campaign cost ceiling after fixture evaluation.
- Signature-music loudness, ducking, looping, fades, and missing-file behavior.

Local-file upload/import is frozen as a post-milestone feature.
