# Automated re-up platform — master todo

Source design: [automated-reup-platform-system-design.md](../reports/automated-reup-platform-system-design.md)

Execution roadmap: [pipeline-delivery-roadmap.md](pipeline-delivery-roadmap.md)

Follow the execution roadmap for milestone order, acceptance gates, and
evidence. The sections below remain the implementation inventory.

Status legend: `[ ]` not started, `[-]` in progress, `[x]` complete, `[!]` blocked.

## Confirmed product decisions

- [x] YouTube receives one whole 16:9 video per source.
- [x] Facebook Pages receive one 9:16 post per chunk.
- [x] TikTok receives one 9:16 draft per chunk for the MVP.
- [x] One Telegram approval authorizes the whole selected campaign fan-out.
- [x] Facebook destinations are administered Pages.
- [ ] Confirm Shopee Affiliate Open API `app_id` and `secret_key` availability.
- [ ] Decide whether account selection uses saved groups, manual selection, or both.

## P0 — prove assumptions and contracts

- [ ] [Pipeline delivery roadmap - complete Steps 0-3](pipeline-delivery-roadmap.md)
- [ ] [Foundation and rights](foundation_and_rights/todo.md)
- [ ] [Media variants and manifest](media_variants_manifest/todo.md)
- [ ] [Platform sandbox](platform_sandbox/todo.md)

## P1 — first reliable YouTube vertical slice

- [ ] [Core data model](core_data_model/todo.md)
- [ ] [Social account vault](social_account_vault/todo.md)
- [ ] [n8n-to-app handoff](n8n_app_handoff/todo.md)
- [ ] [Review inbox](review_inbox/todo.md)
- [ ] [Telegram campaign approval](telegram_campaign_approval/todo.md)
- [ ] [Publisher queue and worker](publisher_queue_worker/todo.md)
- [ ] [YouTube whole-video publishing](youtube_whole_video/todo.md)

## P2 — correct assets and Facebook chunks

- [ ] [Facebook chunk publishing](facebook_chunk_publishing/todo.md)

## P3 — commerce data

- [ ] [Shopee affiliate catalog](shopee_affiliate_catalog/todo.md)

## P4 — TikTok draft MVP

- [ ] [TikTok chunk drafts](tiktok_chunk_drafts/todo.md)

## P5 — scale and operate

- [ ] [Operations dashboard](operations_dashboard/todo.md)
- [ ] [Reliability, observability, and retention](reliability_observability_retention/todo.md)

## Dependency path

```mermaid
flowchart LR
    A[Foundation] --> B[Platform sandbox]
    A --> C[Core data model]
    C --> D[Account vault]
    C --> E[n8n handoff]
    E --> F[Review inbox]
    D --> F
    F --> G[Telegram approval]
    G --> H[Publisher worker]
    H --> I[YouTube]
    C --> J[Media variants]
    D --> K[Facebook]
    H --> K
    J --> K
    D --> L[TikTok drafts]
    H --> L
    J --> L
    K --> M[Shopee comments]
    N[Operations UI] --> O[Reliability hardening]
    I --> N
    K --> N
    L --> N
```

## MVP milestone

The first useful release ends at YouTube: a rendered source enters the Inbox,
one Telegram approval is consumed exactly once, one selected channel receives
the whole 16:9 video and thumbnail, and restarts do not lose or duplicate work.
