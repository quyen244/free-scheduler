# TikTok chunk drafts — todo

- [ ] Add TikTok OAuth with `video.upload` and account identity fields.
- [ ] Validate chunk kind, 9:16 asset, codec, size, and ten-minute maximum.
- [ ] Track pending-share capacity conservatively per account.
- [ ] Initialize the inbox upload and persist `publish_id`/upload expiry.
- [ ] Stream file chunks with correct ranges to the returned upload URL.
- [ ] Poll status and distinguish inbox delivery from public publication.
- [ ] Show/send copyable caption and hashtags for each chunk.
- [ ] Add reconnect, upload-expired, cap-reached, and terminal-failure handling.
- [ ] Test three chunks and ensure no whole-video TikTok target can be created.

