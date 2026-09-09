# Media Service

Owns acquisition. **This is the only component in the pipeline that talks to
YouTube** — everything downstream reads what it wrote to the shared volume.
That constraint is the whole reason it exists: before F2, the transcript
service downloaded audio and the render stage would have downloaded the video,
which meant two fetches, twice the 403 exposure, and no single owner of "the
raw file".

No GPU, no CUDA image. It downloads and remuxes, so a `yt-dlp` refresh rebuilds
a ~150 MB layer instead of a multi-gigabyte one.

## What it writes

For each video, under the shared `/data` volume:

```
data/<video_id>/
├── raw.mp4      the muxed video — what the render stage cuts and composites
├── audio.wav    16 kHz mono PCM — what Whisper resamples to anyway
└── meta.json    { video_id, source_url, title, duration_s }
```

`audio.wav` is derived once here rather than per transcription, and it is small
enough to keep after the retention sweep evicts `raw.mp4`. That is what makes a
re-transcription possible without going back to YouTube.

Downloads land on a `raw.part.*` name and are renamed only on success. Writing
straight to `raw.mp4` would leave a plausible-looking file behind if the
process died mid-transfer, and the cache check would then accept it.

A directory counts as cached only when **all three** files are present.

It also writes the `videos` row in `data/pipeline.db` at `stage = 'ingested'` —
on the cache-hit path as well, so a re-run is never invisible to the pipeline.
The stage is never moved **backwards**: re-ingesting a video that already
reached `rendered` refreshes its title and duration and leaves the stage alone.

## API

### `POST /media/jobs` — what n8n calls

Ingest is slow and unpredictable: measured at 86 s for an 89-minute source and
138 s for an 11-minute one, and the ceiling is whatever YouTube feels like
today. n8n's HTTP Request node gives up at 300 s. So the request records a job,
answers at once, and reports back when the work ends.

```bash
curl -X POST http://127.0.0.1:8001/media/jobs \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.youtube.com/watch?v=jNQXAC9IVRw",
       "callback_url": "http://n8n:5678/webhook-waiting/28?signature=313d…"}'
```

```json
{ "job_id": "855197be…", "video_id": "jNQXAC9IVRw", "state": "queued" }
```

`202`, in about 40 ms. `callback_url` is optional — omit it from a terminal,
where there is nothing to resume.

When the work ends, the service `POST`s the finished job there:

```json
{
  "job_id": "855197be…", "video_id": "jNQXAC9IVRw", "kind": "ingest",
  "state": "done", "progress": 1.0, "error": null, "warnings": null,
  "result": {
    "video_id": "jNQXAC9IVRw", "title": "Me at the zoo", "duration_s": 19.0,
    "raw_path": "/data/jNQXAC9IVRw/raw.mp4",
    "audio_path": "/data/jNQXAC9IVRw/audio.wav",
    "cached": false
  }
}
```

Paths are container-absolute. Every service mounts `/data` at the same place,
so they are meaningful to the caller as-is — n8n moves paths, never bytes.

**The callback fires on failure too**, with `state: "failed"`, `result: null`
and a populated `error`. That is the whole point of it. A job that dies quietly
leaves an n8n execution waiting on a resume that will never come: nothing turns
red, nothing alerts, and it is found days later by wondering where a video
went. Three ways a job can die, all of them covered by a test:

| Death | What arrives |
|---|---|
| yt-dlp or ffmpeg fails | `failed`, with the tool's own message |
| the URL is not a YouTube video URL | no job at all — a synchronous `400` |
| the service restarts mid-download | `failed`, "media-service restarted while this job was running" |

That last one is settled at **startup**: anything still marked `running` belongs
to a process that no longer exists, so the next boot fails it and calls back.
It reaps only `kind = 'ingest'` rows — the kinds this service actually
performs — because a service that reaped every unfinished row would kill the
other services' live jobs every time it restarted.

### `GET /jobs/{job_id}`

The same record, read from the same row, so a dropped callback costs a query
rather than a re-download. `404` if there is no such job.

### `POST /download` — the same work, synchronously

Kept for terminal use and for the tests. Same body without `callback_url`;
answers `200` with the `result` object above. Both endpoints call the same
function and both write the `videos` row, so they cannot disagree about what
exists on disk.

Do not call it from n8n. A cache hit returns in **0.14 s**; a miss takes as
long as it takes.

| Status | When |
|---|---|
| `400` | The URL doesn't look like a YouTube video URL. |
| `502` | `yt-dlp` couldn't fetch it (private, deleted, geo-blocked — or see below), or `ffmpeg` couldn't extract the audio. |

### `GET /health`

```json
{"status": "ok"}
```

## If you see `HTTP Error 403: Forbidden`

`yt-dlp` is deliberately unpinned. YouTube changes its anti-bot checks every
few months and a stale release starts failing videos it used to handle fine. A
version five months old was enough to break an ordinary mobile YouTube link.

**Rebuild with `--no-cache` first:**

```bash
docker compose build --no-cache media-service
```

Docker caches the `pip install` layer by the *contents* of `requirements.txt`.
Since that file names no version, an ordinary `docker compose up --build`
reuses whatever release was resolved the first time, forever, silently drifting
stale. `--no-cache` forces pip to re-resolve.

Cookies, a JS runtime (`deno`/`node`), and a PO-token provider
(`bgutil-ytdlp-pot-provider`) were each tried against a real failing video
during an earlier diagnosis and **none of them was the fix** — only the
`yt-dlp` version mattered. Don't reach for those first. If a `--no-cache`
rebuild doesn't clear it, *then* check whether the current release needs one of
those extras for that specific video; its own `--verbose` output says so
directly.

## Tests

They hit the real network, and the job tests hit the real uvicorn process over
a real socket — `TestClient` runs background work inside the request that
started it, so a `202` measured through it would look instant even on a
synchronous implementation. Run them inside the container:

```bash
docker compose exec media-service python -m pytest tests/ -v
```

`pytest` is in `requirements.txt` on purpose: installing it by hand after every
rebuild is a step that gets skipped exactly when it matters.

> **Do not run the suite while a real job is in flight.** Starting the app runs
> the orphan reaper, and from a job row's point of view a second app instance
> is indistinguishable from a restart — it will fail the live job. The same
> applies to editing any `.py` file here: `uvicorn --reload` restarts the worker
> and takes the download with it. The job then calls back as failed rather than
> vanishing, which is the point, but the work is still gone.
