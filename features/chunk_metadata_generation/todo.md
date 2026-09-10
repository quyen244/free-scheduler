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
- [x] Select `gpt-5.6-luna` explicitly. Evidence: user decision on 2026-09-10
  and `features/_shared/decisions.md`.
- [-] Record the measured fixture cost and per-campaign cost ceiling. Measured:
  `$0.003704` for one whole-video result plus three chunk results. The operating
  ceiling remains to be confirmed and enforced.

## Data and service work

- [x] Define versioned Structured Output schemas for YouTube and chunk metadata.
  Evidence: `metadata-service/tests/test_schemas.py`.
- [x] Add explicit visual/platform metadata revisions and generation attempts.
  Evidence: `test_generator.py::test_selective_retry_persists_provenance_and_reuses_selected_results`.
- [x] Add transcript hash, prompt version, model, response ID, usage, and
  deterministic generation key. Evidence: metadata generator Docker tests.
- [x] Add a Docker metadata service using the OpenAI Responses API. Evidence:
  Compose validation, healthy container, and mocked request-contract tests.
- [x] Pass `OPENAI_API_KEY` only to the metadata-service container through a
  git-ignored Compose environment source. Evidence: Compose validation and
  container presence check; the value was not displayed.
- [-] Validate schema, length, prohibited empty fields, contiguous chunk
  identity, Vietnamese signal, generated-URL prohibition, source grounding,
  and the two-emoji maximum. All deterministic checks pass; semantic grounding
  still requires the live quality fixture.
- [x] Retry only invalid or transiently failed content items with bounded
  backoff. Evidence: selective-retry and non-retryable recovery tests.
- [x] Store only the selected result while preserving attempt provenance.
  Evidence: revision reuse, token provenance, and restart recovery tests.

## Workflow and verification

- [ ] Add `Start metadata`, `Wait metadata`, and `Metadata valid?` after `Chunked` and before voice/render.
- [ ] Route exhausted failures to `needs_action` and Telegram notification.
- [x] Test one-, three-, and variable-chunk fixtures. Evidence: parametrized
  Docker generator test for 1, 3, and 4 chunks.
- [-] Test invalid JSON, missing chunk, rate limit, timeout, transcript edit,
  and manual metadata edit. All cases except app-owned manual editing pass in
  Docker.
- [x] Verify secret literals are absent from tracked automation/features/report
  files and metadata-service logs; verify root `.env` is Git-ignored. No raw
  transcript or generated prompt is logged or stored as attempt provenance.
- [x] Reuse duplicate active jobs, close orphaned attempts after restart, expose
  typed safe job failures, and provide selected metadata/attempt/token reads.
  Evidence: metadata-service API and generator Docker tests.

## Live model verification

- [x] The updated API project exposes `gpt-5.6-luna`. A small structured-output
  request and a read-only whole-video plus three-chunk fixture both passed on
  2026-09-10. No fallback model was selected.
