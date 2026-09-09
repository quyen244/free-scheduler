# Whisper Transcript Service

A small FastAPI microservice that turns ingested audio into a transcript and
cuts that transcript into publishable chunks. It transcribes with
`faster-whisper` (GPU-accelerated, with automatic CPU fallback). Built to be
called from a local n8n workflow's HTTP Request node, instead of bundling
Whisper inside the n8n container itself.

## Requirements

- Docker with the NVIDIA Container Toolkit installed and working
  (`docker run --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi`
  should print your GPU). A GPU is not strictly required — the service falls
  back to CPU automatically — but it's much slower.

## Running it

From the **repository root**, not this directory — the composition spans n8n,
`media-service` and this service, and they share the `./data` volume:

```bash
docker compose up -d
```

The service listens on `127.0.0.1:8000` (bound to localhost only — this is a
local dev tool, not meant to be reachable from other machines). n8n reaches it
as `http://whisper-transcript-service:8000` over the compose network. Code changes
under this directory take effect on save without a rebuild, since the source
tree is bind-mounted and `uvicorn` runs with `--reload`. A rebuild is only
needed when `requirements.txt` or the `Dockerfile` itself changes.

- `models/` — Whisper model weights, bind-mounted so they are never
  re-downloaded. Git-ignored (only `.gitkeep` is tracked).
- `/data` — the shared volume. Reads `<video_id>/audio.wav` and
  `<video_id>/meta.json`, written by `media-service`; writes
  `<video_id>/transcript.json`, which is this service's own.
- `/app/shared` — `shared/pipeline_db.py` from the repo root, the one accessor
  for `data/pipeline.db`. Used here to advance `videos.stage` and to write the
  `chunks` rows.

> `videos/` is left over from before F2 and is no longer read by anything.
> Media now lives in `data/<video_id>/`. The old `.mp3` files in there can be
> deleted whenever convenient.

## Configuration

Set these as environment variables (e.g. in `docker-compose.yml`) to
override the defaults:

| Variable | Default | Purpose |
|---|---|---|
| `WHISPER_MODEL` | `base` | Any [faster-whisper model size](https://github.com/SYSTRAN/faster-whisper) (`tiny`, `small`, `medium`, `large-v3`, ...). Bigger = more accurate, slower, more VRAM. The GPU here has 6 GB, so `large-v3` (~3 GB) is the practical ceiling. |
| `MODEL_DIR` | `models` | Where model weights are cached. |
| `DATA_DIR` | `/data` | The shared volume where `media-service` writes. |

## API

### `GET /health`

```bash
curl http://127.0.0.1:8000/health
# {"status": "ok", "model_loaded": true}
```

### `POST /transcribe`

**This service no longer downloads anything.** `media-service` must ingest the
video first; this takes a `video_id` and reads `audio.wav` from the shared
volume.

```bash
# 1. ingest (media-service owns every call to YouTube)
curl -X POST http://127.0.0.1:8001/download \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.youtube.com/watch?v=jNQXAC9IVRw"}'

# 2. transcribe what it wrote
curl -X POST http://127.0.0.1:8000/transcribe \
  -H "Content-Type: application/json" \
  -d '{"video_id": "jNQXAC9IVRw"}'
```

```json
{
  "video_id": "jNQXAC9IVRw",
  "title": "Me at the zoo",
  "language": "en",
  "duration_s": 19.0,
  "transcript": "Alright, so here we are, one of the elephants. ...",
  "segments": [
    {"start": 0.0, "end": 4.0, "text": "Alright, so here we are, one of the elephants."}
  ]
}
```

`language` is detected by Whisper and decides whether the translate stage runs:
`vi` passes straight through, anything else is translated to Vietnamese first.

The same payload is written to `data/<video_id>/transcript.json` and
`videos.stage` moves to `transcribed`. The file is what the later stages read —
chunking, translation and rendering all need segments, and handing thousands of
them back through n8n once per stage moves megabytes to say what the shared
volume already knows.

### `POST /chunk`

Cuts a transcribed video into chunks on Whisper segment boundaries, writes the
`chunks` rows, and moves `videos.stage` to `chunked`. Takes a `video_id`; the
segments come off the volume.

```bash
curl -X POST http://127.0.0.1:8000/chunk \
  -H "Content-Type: application/json" \
  -d '{"video_id": "3gi_15UH9fQ"}'
```

```json
{
  "video_id": "3gi_15UH9fQ",
  "total_chunks": 3,
  "max_chunk_s": 240.0,
  "min_chunk_s": 60.0,
  "chunks": [
    {"idx": 0, "start_s": 0.0, "end_s": 233.0, "duration_s": 233.0,
     "text": "…", "char_count": 3957}
  ]
}
```

| Field | Default | Purpose |
|---|---|---|
| `max_chunk_s` | `240.0` | Ceiling. A chunk is cut when the next segment would carry it past this. |
| `min_chunk_s` | `60.0` | Floor for the **tail**. A short last chunk is fixed by redistributing the last two chunks across their midpoint, not by merging — merging would push the predecessor past the ceiling. A video shorter than the floor is still one whole chunk. |

Re-chunking is safe. Rows are upserted on `(video_id, idx)` and surplus rows
deleted, so a second call with a different threshold leaves one coherent set.
Render state survives only where the boundaries did: a chunk covering a new
span goes back to `pending` with its `final_path` cleared.

Error responses use the same JSON shape on every failure:

| Status | When |
|---|---|
| `400` | The `video_id` is not the shape a YouTube id has. |
| `404` | Nothing has ingested that video (`/transcribe`), or nothing has transcribed it (`/chunk`). **This never falls back to downloading**, on purpose: exactly one component talks to YouTube, and a convenience fallback here would silently make it two. |
| `422` | The transcript has no segments to chunk. |
| `502` | Whisper failed to transcribe the audio. |

```json
{"error": "no audio for 'aaaaaaaaaaa' — POST the url to the media service's /download first"}
```

## Calling it from n8n

If n8n itself runs as a Docker container (check with `docker ps` — on this
machine it does, as `n8nio/n8n`), its HTTP Request node must call
`http://host.docker.internal:8000/transcribe`, **not**
`http://localhost:8000/transcribe` — `localhost` inside the n8n container
means the n8n container itself, not the host machine. If n8n instead runs
natively on the host (e.g. via `pnpm dev:be`), `localhost:8000` works
directly.

## Download problems

Not this service's concern any more — `yt-dlp` is not even installed here.
See [`media-service/README.md`](../media-service/README.md), which owns every
call to YouTube and carries the 403 troubleshooting notes.

## Running the tests

The `tests/` directory contains real end-to-end checks (they hit the actual
network and, for transcription, the actual GPU) — run them inside the
container, not on the host, since local Python environments don't reliably
have working CUDA library paths:

```bash
docker compose exec whisper-transcript-service python3 -m pytest tests/ -v
```

`pytest` is in `requirements.txt` on purpose: a container recreate wipes
anything pip-installed by hand, and that is exactly when the tests matter.
