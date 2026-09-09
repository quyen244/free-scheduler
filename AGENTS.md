<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

<!-- BEGIN:reup-project-agent-rules -->

# Re-up platform project instructions

The user's explicit instructions take precedence over this file. If a request
changes a confirmed product decision, update the decision documents before
changing implementation code.

## Product goal

Build a local-first automated video re-up system. n8n produces media. The app
reviews campaigns and owns publishing state. A worker publishes through
official platform APIs after one campaign-level Telegram approval.

The confirmed delivery contract is:

- YouTube receives one complete edited 16:9 video per selected channel.
- Facebook Pages receive every 9:16 chunk as a separate post.
- TikTok receives every 9:16 chunk as an inbox draft for the MVP.
- One Telegram approval authorizes the complete selected campaign fan-out.
- Facebook can add configured text comments, a QA or donation image, and an
  approved Shopee affiliate link after a successful video post.
- TikTok caption, hashtags, cover, and final publication are completed manually
  during the draft flow unless a later confirmed decision changes this rule.
- The system must support 5-10 accounts per platform without mixing credentials,
  assets, results, or retries between accounts.

For `N` chunks, the expected target count is:

```text
youtube_accounts + (N * facebook_accounts) + (N * tiktok_accounts)
```

## Authoritative project documents

Read these before planning or implementing a feature that they affect:

- `features/_shared/decisions.md` for confirmed and pending decisions.
- `features/_shared/delivery_contract.md` for source-to-platform mapping.
- `features/todo.md` for priority and dependency order.
- The selected feature's `spec.md` and `todo.md` before changing that feature.
- `reports/automated-reup-platform-system-design.md` for system boundaries and
  architecture.
- `knowledge/n8n-workflows-database-and-json.md` before changing, importing, or
  exporting an n8n workflow.

If these files disagree, flag the conflict and resolve it in `_shared` first.

## How to work with the user

- Use simple, direct English. Explain unfamiliar concepts briefly with concrete
  examples.
- State the result or recommendation first. Add implementation detail only when
  it helps the user decide or verify something.
- Use small diagrams for multi-stage pipelines, branching behavior, data flow,
  and system boundaries when a diagram is clearer than prose.
- Decompose work into trackable units that are small enough to verify but large
  enough to deliver a meaningful outcome.
- Ask focused product questions when the answer changes data models, workflow
  behavior, platform behavior, or acceptance criteria. Inspect available code
  and documents first so the question includes concrete evidence.
- Make safe, reversible assumptions when they do not change product behavior.
  State material assumptions explicitly.
- When asked to change or build something, carry it through implementation and
  relevant verification. Do not stop after giving a plan.
- Do not commit, push, deploy, activate workflows, publish social posts, or send
  external messages unless the user explicitly requests that action.

## Planning and task tracking

- Work from `features/`. Pick tasks whose dependencies are complete.
- Update the feature-local `todo.md` while implementing it.
- Mark a task complete only after its `Done when` contract is verified.
- Record concise evidence beside completed work: a test name, execution ID,
  external sandbox ID, screenshot, or report path.
- Prioritize a thin end-to-end path and correctness gates before dashboards,
  account scale, or cosmetic work.
- Validate with one account per platform before expanding to multiple accounts.
- Keep Shopee integration behind confirmed API credentials and a working base
  Facebook comment flow.

## Architecture rules

- Keep n8n as the media-production orchestrator. Do not make n8n the durable
  publishing queue or the system of record for campaign state.
- Keep the Next.js app as the review, campaign, account, approval, and publishing
  state owner.
- Run platform publishing in a separate worker through provider adapters.
- Use PostgreSQL as the initial durable queue. Add Redis only when measured load
  justifies it.
- Use an outbox for reliable state-to-notification handoff.
- Use explicit state machines, deterministic idempotency keys, leases, retry
  classification, reconciliation, and append-only audit events.
- Keep platform-specific behavior behind adapter interfaces. Domain code must
  reject a whole-video TikTok/Facebook target and a chunked YouTube target.
- Any change to assets, metadata, products, schedule, or selected accounts must
  create a new campaign revision and invalidate the previous approval.
- Prefer official APIs. Browser and cookie automation are outside the MVP path.

## Media correctness gates

A render is not complete because a database row says `rendered`. Before review,
probe every required file and verify that it exists, opens successfully, has an
audio and video stream, uses the expected dimensions and codec, matches the
expected duration, and has a recorded size and SHA-256 checksum.

For a source with `N` chunks, the manifest must contain:

- One valid 1920x1080 whole edited video for YouTube.
- `N` valid 1080x1920 edited chunk videos for Facebook and TikTok.
- Stable asset identifiers, paths, checksums, probe metadata, and warnings.

Use the manifest as the delivery contract. Do not infer asset meaning from an
ambiguous filename such as `final.mp4`.

## Docker and automation rules

- The automation stack lives in `automation/` and runs in Docker Compose.
- Run automation service tests inside their Compose containers. Do not install
  their Python dependencies on the host merely to run tests.
- Preserve the external `n8n_data` volume. Never recreate, delete, or prune it
  as part of routine development.
- Treat `/home/node/.n8n/database.sqlite` in the `n8n_data` volume as the live
  n8n editing and execution state.
- Treat `automation/workflows/reup-pipeline.json` as the intended canonical Git
  export once it exists. Phase files such as `f3` through `f7` are historical
  snapshots.
- The live database and JSON files do not synchronize automatically. Export the
  live workflow before editing workflow JSON. Review IDs and differences before
  importing. Never import an older phase snapshot over a newer live workflow.
- Keep model weights, media, runtime databases, cookies, tokens, and generated
  files out of Git and Docker image layers.
- Do not print secrets or decrypted credentials in commands, logs, reports, or
  test evidence.

## Verification and definition of done

- Test the smallest affected unit first, then the relevant integration path.
- For automation changes, validate Compose configuration and container health.
- For media changes, run Docker tests plus `ffprobe` and inspect representative
  frames for both 16:9 and 9:16 outputs.
- For queue or publishing changes, test duplicate delivery, process restart,
  stale lease, retry, and partial failure behavior.
- For platform changes, use private, unpublished, draft, or sandbox delivery
  before public publishing.
- Report what was verified, what remains unverified, and any external account or
  credential dependency. Do not describe a task as complete when required
  evidence is missing.

<!-- END:reup-project-agent-rules -->
