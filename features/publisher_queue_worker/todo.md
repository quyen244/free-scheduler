# Publisher queue and worker — todo

- [ ] Define claim query, lease duration, heartbeat, and ownership fields.
- [ ] Create standalone worker entry point and lifecycle.
- [ ] Define the provider-adapter interface.
- [ ] Add attempt records and redacted request/response summaries.
- [ ] Classify transient, credential, validation, policy, and unknown errors.
- [ ] Add exponential backoff with jitter and `Retry-After` support.
- [ ] Add reconciliation and dead-letter states.
- [ ] Remove scheduling/publishing work from GET handlers.
- [ ] Add crash, concurrent-claim, lease-expiry, and duplicate-delivery tests.

