# Re-up automation

This folder is the Docker deployment unit for the local video re-up pipeline.
It contains n8n workflow files, four media services, shared pipeline code, local
model files, and runtime data.

## Start the pipeline

Run these commands from this folder:

```powershell
docker compose up -d
docker compose ps
```

The default stack uses NVIDIA GPU access for Whisper and video rendering. The
Docker host must have working NVIDIA container support.

Open n8n at `http://localhost:5678`. The existing external `n8n_data` volume
stores the n8n database and credentials. Moving this folder does not recreate
or delete that volume.

To import the latest workflow file:

```powershell
docker compose exec n8n n8n import:workflow --input=/workflows/f7-render.json
```

## Folder map

| Path | Purpose |
| --- | --- |
| `docker-compose.yml` | Starts n8n and all pipeline services. |
| `workflows/` | Version-controlled n8n workflow exports. |
| `shared/` | Shared job database and callback code. |
| `media-service/` | Downloads and prepares the source media. |
| `whisper-transcript-service/` | Transcribes and splits the source video. |
| `translate-service/` | Translates transcripts into Vietnamese. |
| `render-service/` | Generates speech and renders final media. |
| `data/` | Shared runtime files, presets, and the pipeline database. |

Large model files and generated media stay local and are ignored by Git. The
small preset definitions and database schema remain version-controlled.

## Stop the pipeline

```powershell
docker compose down
```

This removes the containers and Compose network. It does not delete the
external `n8n_data` volume or files under `data/`.
