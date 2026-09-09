# n8n-to-app handoff — todo

- [ ] Define and version the render-complete payload schema.
- [ ] Add timestamp, nonce/idempotency key, and HMAC verification.
- [ ] Add `/api/internal/render-complete`.
- [ ] Canonicalize and allowlist media paths.
- [ ] Verify manifest checksums and probe metadata.
- [ ] Upsert source/items/assets and outbox atomically.
- [ ] Add the final n8n HTTP node after `Rendered`.
- [ ] Test duplicate, reordered, expired, tampered, and missing-file callbacks.

