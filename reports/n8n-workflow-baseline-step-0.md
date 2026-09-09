# n8n workflow baseline - Step 0 evidence

Verified: 2026-09-09

## Outcome

The latest saved `reupPipeline` workflow was exported from the live n8n
database to:

```text
automation/workflows/reup-pipeline.json
```

This file is now the canonical Git export. The `f3` through `f7` files are
historical phase snapshots.

## Canonical workflow

| Field | Value |
| --- | --- |
| Workflow ID | `reupPipeline` |
| Workflow name | `video editing` |
| Active | `false` |
| Saved nodes | 30 |
| Connection sources | 24 |
| Nodes with credential references | 4 |
| Live update time | `2026-09-09T13:10:11.234Z` |
| Export SHA-256 | `1AC605DCC8CF97D560B88540A61E669D019FFD1923A59229E4D3584D48E03E24` |

The export contains credential references for four Telegram nodes. A
disposable round-trip test confirmed that these references survive import and
export. The export does not contain top-level credential data or decrypted
credential values.

The file was also checked without printing values for common secret formats:
Telegram bot tokens, bearer tokens, private keys, and Google API keys. No match
was found.

## Differences from `f7-render.json`

The canonical workflow has six nodes that do not exist in the F7 snapshot:

- `Code in JavaScript` extracts a URL from an incoming Telegram message.
- `If` checks that the extracted URL is present.
- `Send a text message` asks the operator to attach a URL.
- `Send a text message1` reports translation failure.
- `Send a text message2` reports voice failure.
- `Send a text message3` reports render failure.

No F7 node was removed.

Connection changes:

- `Webhook -> Start ingest` was replaced by
  `Webhook -> Code in JavaScript -> If -> Start ingest`.
- The false input-validation branch now sends a Telegram message.
- Translation, voice, and render failure nodes now send Telegram messages.

Other changes:

- `Start ingest` now reads the validated `$json.videoUrl`; F7 read
  `$json.body.url` directly.
- The Webhook node has a new node ID and webhook ID while keeping the same
  `reup-pipeline` path.
- All 24 common nodes were repositioned in the editor.
- Empty notes were removed from two failure nodes. This does not change runtime
  behavior.

## Disposable import test

The canonical export was imported into a new temporary n8n database stored in
the task-owned Docker volume `reup_step0_verify_20260909`. It was exported again
and compared with the source after sorting JSON object keys.

Verified results:

- Workflow ID matched.
- All 30 node IDs and node definitions matched.
- All connections matched.
- Workflow settings matched.
- All four credential references matched.
- The temporary container and volume were removed after verification.

The live `n8n_data` volume was not modified by this test.

## Docker verification

`docker compose config --quiet` passed from `automation/`.

All services reported `running` and `healthy`:

- `n8n`
- `media-service`
- `whisper-transcript-service`
- `translate-service`
- `render-service`

## Safe workflow lifecycle

```text
Edit and save in n8n
  -> export workflow ID reupPipeline
  -> review automation/workflows/reup-pipeline.json
  -> commit the JSON export
  -> import that canonical file on another installation
```

The database and JSON file do not synchronize automatically. Export before
editing the JSON, and compare before every import.
