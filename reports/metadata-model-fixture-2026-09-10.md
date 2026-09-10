# Metadata model fixture - 2026-09-10

## Result

`gpt-5.6-luna` is accessible with the updated server-side API key and remains
the selected metadata model. No fallback model was configured or called.

The official model documentation describes Luna as the GPT-5.6 model for
cost-sensitive, high-volume workloads and lists Responses API and Structured
Outputs support. Pricing at verification time was `$0.20` per million input
tokens and `$1.20` per million output tokens:

https://developers.openai.com/api/docs/models/gpt-5.6-luna

## Live checks

### Small structured-output fixture

- Response ID: `resp_06b46a22dc6018c6016aa25681ce4487d0b7281616be369590`
- Input: 441 tokens
- Output: 221 tokens
- Total: 662 tokens
- Estimated cost: `$0.0003534`
- Result: valid `metadata.v1` Vietnamese YouTube object

### Representative read-only fixture

- Source: existing translated fixture `3gi_15UH9fQ`
- Shape: one whole-video YouTube object plus three independent chunk objects
- Input: 10,048 tokens
- Output: 1,412 tokens
- Total: 11,460 tokens
- Estimated cost: `$0.003704`
- Reasoning effort: `none`
- Result: all four objects passed schema, Vietnamese-signal, hashtag, emoji,
  URL, and chunk-identity validation
- Mutation: none; the fixture did not create jobs/revisions or change chunk,
  render, or n8n state

Cost calculation:

```text
(10,048 * $0.20 + 1,412 * $1.20) / 1,000,000 = $0.003704
```

## Remaining decision

Set a per-campaign cost ceiling and decide whether it should be a hard stop or
an alert threshold. A `$0.02` ceiling would provide retry headroom over this
measured three-chunk fixture, but it is not yet confirmed or enforced.
