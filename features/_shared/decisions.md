# Confirmed product decisions

Last updated: 2026-09-09

- YouTube publishes one whole 16:9 video per source.
- Facebook publishes each 9:16 chunk as a separate post.
- Facebook destinations are Pages administered by the operator.
- TikTok uploads each 9:16 chunk as a draft for the MVP.
- TikTok caption, hashtags, cover, and final publication are completed manually
  in the TikTok app during the draft flow.
- One Telegram approval temporarily authorizes the whole campaign fan-out across
  all selected accounts and platforms.
- Any asset, metadata, product, schedule, or target-account change creates a new
  campaign revision and invalidates prior approval.
- n8n owns media production; the Next.js app owns review and publishing state;
  a separate worker calls platform APIs.
- PostgreSQL is the initial durable publish queue. Redis is deferred until load
  proves it necessary.
- Official APIs are preferred. Browser/cookie automation is not an MVP path.

## Pending decisions

- Whether Shopee Affiliate Open API credentials are available.
- Whether account targeting supports manual selection, saved groups, or both.
- Final duration/safe-zone rules for Facebook chunks.

