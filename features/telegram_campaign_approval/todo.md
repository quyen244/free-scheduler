# Telegram campaign approval — todo

- [ ] Add Telegram bot/chat allowlist configuration.
- [ ] Add outbox dispatcher for review-ready notifications.
- [ ] Generate opaque one-use approval IDs and store only their hash.
- [ ] Build compact message with source, chunk count, accounts, warnings, and target count.
- [ ] Add Approve, Reject, and Open inline buttons.
- [ ] Verify chat, operator, expiry, revision, and preflight atomically.
- [ ] Create all campaign targets in the approval transaction.
- [ ] Answer callback and edit the Telegram message.
- [ ] Test duplicate taps, old messages, concurrent decisions, and bot retries.
