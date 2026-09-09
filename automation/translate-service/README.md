# Translate Service

Turns a Whisper transcript into Vietnamese, one segment at a time, keeping the
timings exactly. Runs `HY-MT1.5-1.8B` (Q4_K_M) on the CPU through
`llama-cpp-python`.

Called from n8n only when Whisper detected something other than `vi`. Sources
here are English and Chinese, and this model translates both natively — no
double hop through English.

## Why it is its own service

The GPU is Whisper's. This box has one 6 GB card, and a 1.8B translator
competing for that VRAM would slow the stage that matters more. Q4_K_M on CPU
runs at roughly **1.5 s per segment**, which is fast enough for something that
runs once per video and is why the image carries no CUDA at all.

## Running it

From the **repository root** — the composition spans n8n and three services
sharing `./data`:

```bash
docker compose up -d
```

The service listens on `127.0.0.1:8002`; n8n reaches it as
`http://translate-service:8002` over the compose network.

- `models/` — the `.gguf`, bind-mounted so it is never re-downloaded and never
  committed. See the licence note below.
- `/data` — the shared volume. Reads `<video_id>/transcript.json`, writes
  `<video_id>/transcript.vi.json`.
- `/app/shared` — `shared/pipeline_db.py` from the repo root, the one accessor
  for `data/pipeline.db`.

**No `--reload` here**, unlike the other two services. Their work finishes
inside the request; this one's runs for minutes in the background, and a reload
lands either as a graceful shutdown that blocks the port until the job drains
or as a restart that strands the row at `running`. After editing, restart it:

```bash
docker compose restart translate-service
```

### Getting the weights

Not in git — 1.1 GB, and not redistributable (below). Fetch once:

```bash
curl -fL -o translate-service/models/HY-MT1.5-1.8B-Q4_K_M.gguf \
  "https://huggingface.co/tencent/HY-MT1.5-1.8B-GGUF/resolve/main/HY-MT1.5-1.8B-Q4_K_M.gguf?download=true"
```

> **Licence.** `HY-MT1.5` ships under the *Tencent HY Community License*, which
> is not an open-source licence: it carries an acceptable-use policy and
> expressly does not apply in the EU, UK or South Korea. Local use without
> redistributing the weights is fine; **the `.gguf` must never be committed to
> this public repo.** `tencent/Hy-MT2-1.8B-GGUF` is apache-2.0, same size, same
> languages, same quant filenames — set `TRANSLATE_MODEL` and that is the whole
> swap.

### Building it

`llama-cpp-python` has no wheel for this platform, so pip compiles it: a
`--no-cache` build is close to **three hours**. The Dockerfile is ordered so
that only a `requirements.txt` change pays that; everything else reuses the
layer.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `TRANSLATE_MODEL` | `models/HY-MT1.5-1.8B-Q4_K_M.gguf` | Path to the weights, relative to `/app`. |
| `DATA_DIR` | `/data` | The shared volume. |
| `LLAMA_THREADS` | `0` (llama.cpp decides) | Generation threads. |
| `LLAMA_CONTEXT` | `2048` | Context window. One segment at a time needs very little of it. |
| `LLAMA_MAX_OUTPUT` | `512` | Ceiling on one segment's translation. |
| `TRANSLATE_BUDGET` | `1` | The second pass. `0` restores single-pass behaviour byte for byte. |
| `TRANSLATE_TARGET_RATIO` | `1.35` | The speed-up a segment may need before it counts as too long. Matches `TTS_MAX_RATIO`. |
| `TRANSLATE_RETRIES` | `2` | Seeds the second pass may try on a segment that does not fit. |

## Translating into the time the segment has

Vietnamese runs longer than English - a median 1.29x the character count on the
reference run - and the source leaves almost no silence to absorb it. The voice
stage then speeds those segments up with `atempo`, and past about 1.35x they
sound rushed. On the reference run 23 of 104 segments were over that line and
the worst needed 1.62x.

So the translator asks how much Vietnamese fits in the time the source gives a
segment, and re-translates the ones that do not fit. Two passes:

1. **Every segment, exactly as before.** Same prompt, same seed, same sampling.
2. **Only the segments that miss their slot**, with a character budget stated in
   the prompt and a fresh seed. The budget comes from a duration model fitted on
   measured TTS output (`budget.py`, R^2 0.954).

The second pass can only improve the result. A candidate that is longer, empty,
truncated at its token cap, or suspiciously far under its budget is discarded
and the first pass stands. `translate` never fails because of it.

**It does not touch the timeline.** `start` and `end` are copied from the source
at both passes; only the words change. The segment count is checked again after
the second pass, because that count is what every later stage trusts.

Measured on the reference run at the shipped 1.35: segments needing more than
1.35x fell from 23 to 8, the worst from 1.85x to 1.65x, for +111 s on the stage.
Evidence and the rejected 1.25 arm are in `reports/experiment-budget-ratio.md`.

## API

### `GET /health`

```bash
curl http://127.0.0.1:8002/health
# {"status": "ok", "model_loaded": false}
```

`model_loaded` is reported, not required. The weights load on the first job
rather than at startup: holding the healthcheck down for a 17-second load on
every restart buys nothing.

### `POST /translate/jobs`

```bash
curl -X POST http://127.0.0.1:8002/translate/jobs \
  -H "Content-Type: application/json" \
  -d '{"video_id": "3gi_15UH9fQ"}'
# 202 {"job_id": "...", "video_id": "3gi_15UH9fQ", "state": "queued"}
```

Async for the same reason ingest is: 104 segments at ~1.5 s each is minutes,
and n8n's HTTP node gives up at 300 s. Pass `callback_url` (n8n's Wait-node
resume URL) and the finished job is POSTed there — on failure too, because a
job that dies silently parks an execution forever.

The result lands at `data/<video_id>/transcript.vi.json`, `videos.stage` moves
to `translated`, and the job result carries:

```json
{
  "video_id": "3gi_15UH9fQ",
  "language": "vi",
  "source_language": "en",
  "total_segments": 104,
  "transcript_path": "/data/3gi_15UH9fQ/transcript.vi.json"
}
```

Written **beside** `transcript.json`, never over it: the source text is what a
mistranslation is diagnosed against, and re-transcribing to get it back costs a
GPU minute for nothing.

### `GET /jobs/{job_id}`

The same record the callback carried, read back from the database — so a
workflow that missed its callback can still find out what happened. `404` for
an unknown id.

| Status | When |
|---|---|
| `400` | The `video_id` is not the shape a YouTube id has. |
| `404` | Nothing has transcribed that video, or no such job. |
| `422` | The model returned something that no longer lines up with the input. |
| `502` | The model failed to produce a translation at all. |

## Alignment is the whole point

A segment list that comes back one short shifts every subtitle after it, and
that surfaces two stages later as a voice track out of sync with the picture —
miserable to trace back to here. Two things hold it:

- **One segment per model call.** Asking for a numbered list of 40 lines is
  faster and is exactly how alignment breaks: the model merges two short lines,
  renumbers, or drops one that looked like a duplicate. A per-segment call
  cannot merge anything, so the count is right by construction and the check is
  a backstop rather than the only defence.
- **`start` and `end` are copied, never re-derived.** A matching count with an
  empty string anywhere fails the job rather than shipping a gap.

**The cost of that choice:** a per-segment call has no surrounding context, so a
segment that only makes sense next to its neighbour can drift — an "abandoned
asylum" in one segment becomes "there" in the Vietnamese. Alignment was judged
the more expensive thing to lose. Passing neighbouring segments as prompt
context, while still accepting only one line back, is the fix if the drift
starts to matter.

**Vietnamese input is returned untouched**, even though the n8n branch already
skips the call. A service that would happily re-translate Vietnamese into
Vietnamese when called by hand is one bad IF condition away from garbling a
whole video.

**One llama.cpp context, one lock.** FastAPI runs each background job in its own
thread, and two of them generating on the same context wedges the process: no
error, no progress, both rows stuck at `running`, and `/health` stops answering
so even the container healthcheck cannot see it. Generation is serialised per
line, so a second video queues line by line rather than behind the whole first
video.

## Running the tests

Real network, real model, real socket — inside the container:

```bash
docker compose exec translate-service python -m pytest tests/ -v
```

About six minutes: the suite translates an 11-minute video end to end and then
runs two jobs at once to pin the wedge above. It talks to the running uvicorn
process rather than `TestClient`, which runs background work inside the request
it was started from — a 202 measured through it would look instant however the
endpoint was written.
