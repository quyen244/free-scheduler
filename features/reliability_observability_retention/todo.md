# Reliability, observability, and retention — todo

- [ ] Add trace IDs and append-only audit events across n8n/app/worker boundaries.
- [ ] Add queue-age, latency, retry, failure, and credential-health metrics.
- [ ] Add structured redacted logs and searchable external request IDs.
- [ ] Classify retryable, user-fixable, unknown-outcome, and terminal failures.
- [ ] Add Telegram action-required, partial-failure, and final-summary alerts.
- [ ] Finish n8n error workflow and orphan-job reaping/recovery.
- [ ] Add Retry failed stage and Restart as new campaign commands.
- [ ] Add reference-safe retention: success temp +1d, success source/final +7d,
  failed files +3d, metadata/audit until manual deletion.
- [ ] Prove a cleaned failed source can be downloaded again without losing history.
- [ ] Add backup procedures for PostgreSQL, n8n data, manifests, and media.
- [ ] Document separate encryption-key recovery.
- [ ] Run restore drill on disposable data.
- [ ] Drill worker death, stale lease, token expiry, 429, 5xx, lost callback,
  duplicate callback, unknown upload outcome, and Facebook comment failure.
