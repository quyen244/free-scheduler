# n8n-to-app handoff — todo

- [ ] Define and version the render-complete payload schema, including metadata
  revision, brand-profile IDs, and the branded delivery assets. Carry the
  manifest's `schema_version` and `topology`. Under `media-manifest.v2` /
  `landscape_chunks` that is one 1920x1080 `branded_whole` per brand plus one
  1920x1080 `branded_landscape_chunk` per part, each naming its brand's whole
  in `lineage_asset_id`. Clean-master lineage belongs to v1 history only.
- [ ] Add timestamp, nonce/idempotency key, and HMAC verification.
- [ ] Add `/api/internal/render-complete`.
- [ ] Canonicalize and allowlist media paths.
- [ ] Verify manifest checksums and probe metadata.
- [ ] Upsert source/items/assets and outbox atomically.
- [ ] Add the final n8n HTTP node after `Rendered`.
- [ ] Test duplicate, reordered, expired, tampered, and missing-file callbacks.
