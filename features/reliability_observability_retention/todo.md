# Reliability, observability, and retention — todo

- [ ] Add trace IDs and append-only audit events across n8n/app/worker boundaries.
- [ ] Add queue-age, latency, retry, failure, and credential-health metrics.
- [ ] Add structured redacted logs and searchable external request IDs.
- [ ] Add Telegram terminal-failure and dead-letter alerts.
- [ ] Finish n8n error workflow and orphan-job reaping/recovery.
- [ ] Add reference-safe seven-day raw-source retention sweep.
- [ ] Add backup procedures for PostgreSQL, n8n data, manifests, and media.
- [ ] Document separate encryption-key recovery.
- [ ] Run restore drill on disposable data.
- [ ] Drill worker death, stale lease, token expiry, 429, 5xx, lost callback,
  duplicate callback, unknown upload outcome, and Facebook comment failure.

