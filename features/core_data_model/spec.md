# Core data model

Priority: P1  
Depends on: foundation contract

## What it is

Replace the overloaded `ScheduledPost` concept with durable sources,
submissions, content/metadata revisions, brand profiles, variants, campaigns,
processing attempts, targets, publish attempts, post-actions, approvals, and
events in PostgreSQL.

## How it works

One `SourceVideo` can receive multiple submissions and campaigns. A failed
attempt does not blacklist the source. `WHOLE_VIDEO` has no chunk index;
`CHUNK` does. Campaign revisions select metadata, brands, and variants.
Processing/publishing attempts and post-actions are append-only children.

```mermaid
erDiagram
    SOURCE_VIDEO ||--o{ CONTENT_ITEM : derives
    SOURCE_VIDEO ||--o{ SOURCE_SUBMISSION : identified_by
    CONTENT_ITEM ||--o{ ASSET_VARIANT : has
    CONTENT_ITEM ||--o{ METADATA_REVISION : describes
    SOURCE_VIDEO ||--o{ CAMPAIGN : distributes
    CAMPAIGN ||--o{ CAMPAIGN_REVISION : versions
    CAMPAIGN ||--o{ PROCESSING_ATTEMPT : runs
    BRAND_PROFILE ||--o{ BRAND_ACCOUNT : groups
    SOCIAL_ACCOUNT ||--o{ BRAND_ACCOUNT : assigned_to
    BRAND_PROFILE ||--o{ BRAND_MEDIA_ASSET : owns
    CAMPAIGN_REVISION }o--o{ BRAND_PROFILE : selects
    CAMPAIGN ||--o{ PUBLISH_TARGET : fans_out
    CONTENT_ITEM ||--o{ PUBLISH_TARGET : delivers
    PUBLISH_TARGET ||--o{ PUBLISH_ATTEMPT : tries
    PUBLISH_TARGET ||--o{ POST_ACTION : follows
    CAMPAIGN ||--o{ APPROVAL_REQUEST : reviews
```

## Important information

- Additive migrations must preserve existing `ScheduledPost` rows.
- Source identity and campaign execution are separate: active duplicates are
  blocked, but failed/published sources can be intentionally reused.
- Platform-specific metadata is validated, versioned JSON on a target.
- Status transitions occur through domain functions, never arbitrary strings.
- Unique keys enforce one target per campaign revision, content item, and account.

## Done when

Prisma migrations apply to a copy of the current database, old rows remain
readable, and fixtures prove the 1 + N + N target formula without duplicates.
