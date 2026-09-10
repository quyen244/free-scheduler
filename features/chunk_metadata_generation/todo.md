# Chunk and whole-video metadata generation - todo

## Contract

- [x] Confirm generated fields for YouTube and every chunk.
- [x] Confirm chunk transcript plus whole-video summary as context.
- [x] Confirm source-grounded wording, structured validation, selective retry, and one selected result.
- [x] Confirm post metadata edits do not rerender while visual-text edits do.
- [x] Confirm exactly five hashtags: three content-specific plus two relevant
  broad/discovery tags.
- [x] Confirm `OPENAI_API_KEY` is available without recording its value.
- [x] Confirm Vietnamese as the default metadata language.
- [x] Confirm curiosity-driven, engaging, never-misleading copy.
- [x] Confirm zero to two relevant emojis per platform metadata object.
- [x] Confirm cost-first OpenAI model selection through fixture evaluation.
- [ ] Record the selected OpenAI model and per-campaign cost ceiling after the
  fixture evaluation.

## Data and service work

- [x] Define versioned Structured Output schemas for YouTube and chunk metadata.
  Evidence: `metadata-service/tests/test_schemas.py`.
- [ ] Add explicit visual/platform metadata revisions and generation attempts.
- [ ] Add transcript hash, prompt version, model, response ID, usage, and idempotency key.
- [ ] Add a Docker metadata service using the OpenAI Responses API.
- [x] Pass `OPENAI_API_KEY` only to the metadata-service container through a
  git-ignored Compose environment source. Evidence: Compose validation and
  container presence check; the value was not displayed.
- [-] Validate schema, length, prohibited empty fields, chunk identity, source
  grounding, Vietnamese output, and the two-emoji maximum. Structural, length,
  identity, hashtag, and emoji validation are implemented; language quality and
  grounding checks remain.
- [ ] Retry only invalid or transiently failed content items with bounded backoff.
- [ ] Store only the selected result while preserving attempt provenance.

## Workflow and verification

- [ ] Add `Start metadata`, `Wait metadata`, and `Metadata valid?` after `Chunked` and before voice/render.
- [ ] Route exhausted failures to `needs_action` and Telegram notification.
- [ ] Test one-, three-, and variable-chunk fixtures.
- [ ] Test invalid JSON, missing chunk, rate limit, timeout, transcript edit, and manual metadata edit.
- [ ] Verify secrets and raw sensitive prompts are absent from exports and logs.
