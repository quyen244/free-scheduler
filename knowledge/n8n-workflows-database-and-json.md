# How n8n workflows are stored and connected

## Where the workflow lives

```mermaid
flowchart LR
    UI["n8n visual editor"] -->|"Save automatically"| DB[("database.sqlite<br/>Docker volume: n8n_data")]
    DB -->|"n8n export:workflow"| JSON["workflows/*.json"]
    JSON -->|"Git tracks this file"| GIT["Project history"]
    JSON -->|"n8n import:workflow"| DB

    NOTE["The database and JSON files<br/>do not synchronize automatically"]
    NOTE -.-> DB
    NOTE -.-> JSON
```

This project has two workflow locations:

- Live runtime: `/home/node/.n8n/database.sqlite` inside the n8n container.
- Exported files: `automation/workflows/*.json`.

The live database is stored in the external Docker volume named `n8n_data`.
Recreating the n8n container does not remove that volume.

The workflow folder is mounted read-only at `/workflows` inside the container.
The JSON files are backups and deployment artifacts. n8n does not load or
synchronize them automatically. A file only enters the database when it is
explicitly imported.

## Current canonical candidate

The live database workflow was exported and round-trip verified on September 9,
2026. On September 10, the canonical JSON was extended with the reviewed
metadata gate and media-revision render handoff. It has not been imported into
the live database.

| Version | Nodes | Last update |
| --- | ---: | --- |
| Live database `video editing` | 30 | September 10, 2026 export |
| Canonical `reup-pipeline.json` | 36 | September 10, 2026 media-revision candidate |
| `f7-render.json` | 24 | September 7, 2026 |
| Live-to-canonical difference | 6 metadata nodes | Import pending |

The live export also differs from canonical input handling: live still sends
`videoUrl` as the invalid-input Telegram `chatId`, while canonical keeps the
corrected source `chatId` and Vietnamese guidance. Do not overwrite that fix
with the older live value.

The six canonical metadata nodes are:

- `Start metadata`
- `Wait for metadata`
- `Metadata valid?`
- `Metadata selected`
- `Metadata failed`
- `Metadata needs action`

Do not import `f7-render.json` over the current workflow. It is an older phase
snapshot. Use `reup-pipeline.json` as the reviewed import target.

All workflows were inactive at the time of inspection. An inactive workflow
can be edited and tested manually, but its production webhook is not active.

## Prepared canonical flow

```mermaid
flowchart LR
    C[Chunked] --> S[Start metadata]
    S --> W[Wait for metadata]
    W --> V{Job done and revision selected?}
    V -->|Yes| M[Metadata selected]
    M --> VO[Start voice]
    V -->|No| F[Metadata failed]
    F --> T[Telegram needs action]
```

The canonical JSON passed eleven structural contract tests and imported into an
isolated n8n database. This validation did not touch the live `n8n_data` volume.

After voice generation, the canonical `Start render` node calls
`/media-revision/jobs`. Its success gate requires a completed job, a `ready`
manifest, and zero asset failures; the output is the manifest path rather than
the legacy ambiguous `processed_path`.

## Current live workflow

```mermaid
flowchart TD
    W["Webhook"] --> JS["Code in JavaScript<br/>Validate request"]
    JS --> VALID{"If<br/>Request valid?"}

    VALID -- No --> TG0["Telegram message<br/>Input rejected"]
    VALID -- Yes --> SI["Start ingest"]
    SI --> WI["Wait for ingest"]
    WI --> IO{"Ingest ok?"}

    IO -- No --> IF["Ingest failed"]
    IO -- Yes --> ING["Ingested"]
    ING --> TRANS["Transcribe"]
    TRANS --> VI{"Vietnamese?"}

    VI -- Yes --> CHUNK["Chunk"]
    VI -- No --> ST["Start translate"]
    ST --> WT["Wait for translation"]
    WT --> TO{"Translate ok?"}
    TO -- No --> TF["Translate failed"]
    TF --> TG1["Telegram message"]
    TO -- Yes --> CHUNK

    CHUNK --> CHUNKED["Chunked"]
    CHUNKED --> SV["Start voice"]
    SV --> WV["Wait for voice"]
    WV --> VO{"Voice ok?"}

    VO -- No --> VF["Voice failed"]
    VF --> TG2["Telegram message"]
    VO -- Yes --> VOICED["Voiced"]

    VOICED --> SR["Start render"]
    SR --> WR["Wait for render"]
    WR --> RO{"Render ok?"}

    RO -- No --> RF["Render failed"]
    RF --> TG3["Telegram message"]
    RO -- Yes --> DONE["Rendered"]
```

## How a node is defined

A workflow JSON file has two important sections:

```json
{
  "nodes": [],
  "connections": {}
}
```

An actual Webhook node from this pipeline looks like this:

```json
{
  "id": "a1000000-0000-4000-8000-000000000001",
  "name": "Webhook",
  "type": "n8n-nodes-base.webhook",
  "typeVersion": 2.1,
  "position": [-200, 0],
  "parameters": {
    "httpMethod": "POST",
    "path": "reup-pipeline"
  }
}
```

The fields mean:

- `id`: stable internal identity for the node.
- `name`: visible name and the name used by connections.
- `type`: implementation that n8n runs.
- `typeVersion`: version of that node implementation.
- `position`: location of the node in the visual editor.
- `parameters`: configuration that controls what the node does.

## How nodes connect

This connection sends the Webhook output to the Start ingest input:

```json
{
  "Webhook": {
    "main": [
      [
        {
          "node": "Start ingest",
          "type": "main",
          "index": 0
        }
      ]
    ]
  }
}
```

Read it as:

```text
Webhook output 0 -> Start ingest input 0
```

For an `If` node:

```text
main[0] = true output
main[1] = false output
```

For example:

```json
{
  "Ingest ok?": {
    "main": [
      [{ "node": "Ingested", "type": "main", "index": 0 }],
      [{ "node": "Ingest failed", "type": "main", "index": 0 }]
    ]
  }
}
```

```mermaid
flowchart LR
    IF{"Ingest ok?"}
    IF -- "main[0]: true" --> OK["Ingested"]
    IF -- "main[1]: false" --> FAIL["Ingest failed"]
```

During execution, nodes normally pass arrays of JSON items to the next node.
Expressions such as `$json` read the current item. Expressions such as
`$node["Node name"]` read data from another node in the same execution.

## Export the latest database workflow

Run these commands from `automation/`:

```powershell
docker compose exec -T n8n n8n export:workflow `
  --id=reupPipeline `
  --pretty `
  --output=/tmp/reup-pipeline.json

docker cp n8n:/tmp/reup-pipeline.json `
  .\workflows\reup-pipeline.json
```

This creates the following host file:

```text
automation/workflows/reup-pipeline.json
```

The `/workflows` mount is read-only inside the container. The command therefore
exports to `/tmp` first and then copies the result to the host folder.

## Import a workflow file into the database

Run this from `automation/`:

```powershell
docker compose exec -T n8n n8n import:workflow `
  --input=/workflows/reup-pipeline.json
```

Importing changes the database. Check the file first so an old export does not
replace a newer live workflow.

## Recommended workflow lifecycle

Use one canonical file named `automation/workflows/reup-pipeline.json`:

```text
Edit in n8n
    -> save to the live database
    -> test the workflow
    -> export reupPipeline
    -> review reup-pipeline.json
    -> commit the JSON file
```

This makes the responsibilities clear:

- The database is the live editing and execution source.
- `reup-pipeline.json` is the version-controlled deployment source.
- Phase files such as `f5`, `f6`, and `f7` are historical snapshots.
