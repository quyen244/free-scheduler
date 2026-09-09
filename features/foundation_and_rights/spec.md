# Foundation and rights

Priority: P0  
Depends on: nothing

## What it is

Freeze the rules every later feature relies on: lawful source use, delivery-unit
mapping, platform/account selection, and immutable approval revisions.

## How it works

Every source carries `rights_status`. Only `owned`, `licensed`,
`public_domain`, or `permission` may enter review. A campaign maps one source
to one whole YouTube item and N Facebook/TikTok chunk items per selected brand
profile. Any material edit increments the revision and invalidates approval.

```mermaid
flowchart LR
    S[Source submitted] --> R{Rights known?}
    R -- no --> B[Blocked]
    R -- yes --> M[Build delivery map]
    M --> V[Create revision]
    V --> Q[Eligible for review]
```

## Important information

- Transforming or translating copied media does not establish copyright rights.
- The upload-unit mapping is a backend invariant, not merely a UI default.
- The YouTube whole output preserves original visuals but uses the edited
  Vietnamese voice, subtitles, brand watermark, and quiet signature music.
- One Telegram approval covers the selected campaign temporarily.
- Unknown rights is a validation failure, not a warning.

## Done when

One written one-brand fixture with three chunks deterministically maps to one
YouTube item, three Facebook items, and three TikTok items; variable chunk counts
use the same formula; an unknown-rights fixture cannot be approved.
