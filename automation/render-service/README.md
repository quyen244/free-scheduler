# Render Service

Stages 5 and 6 of the pipeline: a Vietnamese voice track laid on the source's
own timeline, and the ffmpeg composite that turns a chunk of source video into
something uploadable.

Two stages, one service, because they have to agree on timing to the
millisecond and a process boundary between them is a boundary that agreement
has to cross. They are separate endpoints so a preset change costs a re-render
and not another twenty minutes of synthesis.

- `POST /voice/jobs` — ZeroTTS per Whisper segment, fitted to the slot Whisper
  measured. Writes `data/<video_id>/voice.wav` and `voice.json`.
- `POST /render/jobs` — blur, background composite, logo, watermark, burned
  subtitles, the voice track under it. Writes
  `data/<video_id>/chunks/<NNN>/final.mp4` and `processed/final.mp4`.

## Running it

From the **repository root** — the composition spans n8n and four services
sharing `./data`:

```bash
docker compose up -d
```

The service listens on `127.0.0.1:8003`; n8n reaches it as
`http://render-service:8003` over the compose network.

- `models/` — the ZeroTTS weights, ~900 MB, pulled from the HF Hub on first use
  and bind-mounted so a rebuild does not fetch them again. Never committed.
- `/data` — the shared volume. Reads `transcript.vi.json` (or `transcript.json`),
  `raw.mp4` and `presets/`; writes `voice.wav`, `voice.json` and the chunk
  outputs.
- `/app/shared` — `shared/pipeline_db.py` from the repo root.

**No `--reload`**, for the same reason the translate service has none: the work
runs for tens of minutes in a background task, and a reload lands either as a
graceful shutdown that holds the port until the job drains or as a restart that
strands the row at `running`. After editing:

```bash
docker compose restart render-service
```

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `DATA_DIR` | `/data` | The shared volume. |
| `TTS_MODEL_DIR` | `/models` | Where the ZeroTTS weights are cached. Exported as `HF_HOME`. |
| `TTS_VOICE` | `maichi` | Default voice, one of the eight below. |
| `TTS_THREADS` | `4` (compose sets `2`) | ONNX Runtime intra-op threads per session. **More is slower** — see below. |
| `TTS_WORKERS` | `1` (compose sets `1` on CUDA, `2` on the CPU image) | How many ONNX sessions synthesise at once. Concurrency comes from here, not from threads. On CUDA the sessions share one card instead of idle cores, so one is what `reports/tts-spike.md` measured. |
| `TTS_PROVIDER` | `cpu` (compose sets `cuda`) | The ONNX Runtime execution provider, asked for by name. The loader raises when the sessions did not get it: ONNX Runtime builds a CPU session without complaining when the CUDA libraries are missing, and the stage is then slow for no visible reason. |
| `DEFAULT_PRESET` | `bi-mat-bi-an` | Preset a render falls back to. |
| `TTS_MIN_RATIO` | `0.75` | Below this a segment barely covers its slot — reported. |
| `TTS_MAX_RATIO` | `1.35` | Above this a segment sounds rushed — reported. |

## The voice half

### `POST /voice/jobs`

```bash
curl -X POST http://127.0.0.1:8003/voice/jobs \
  -H "Content-Type: application/json" \
  -d '{"video_id": "3gi_15UH9fQ"}'
# 202 {"job_id": "...", "video_id": "3gi_15UH9fQ", "state": "queued"}
```

Async, and the longest job in the pipeline. Pass `callback_url` (n8n's
Wait-node resume URL) and the finished job is POSTed there — on failure too,
because a job that dies silently parks an execution forever. `progress` on the
job row moves as segments complete, so a slow job is distinguishable from a
stuck one.

Writes `data/<video_id>/voice.wav` — full length, aligned to the **source's**
timeline — and `voice.json`, which records what happened to every segment:

```json
{ "idx": 41, "start": 262.5, "end": 268.1, "slot_s": 5.6,
  "spoken_s": 7.9, "ratio": 1.41, "atempo": 1.41,
  "warning": "segment 41 at 262.5s needs 1.41x to fit 5.6s — above the 1.35x limit, so it will sound rushed" }
```

### Timing is the whole point

A voice track that drifts is the failure this stage exists to prevent, and it
is invisible until somebody watches the finished video. Three things hold it:

- **Absolute placement, never concatenation.** Each segment is written into a
  silent track at `round(start × 48000)` samples. Concatenating would make
  every segment's position depend on the length of everything before it, so one
  segment synthesised 200 ms long would shift the rest of the video and the
  error would accumulate.
- **The model's own leading silence is trimmed first.** Otherwise the placement
  is exact and the *words* still start late by however much padding the model
  prepended.
- **Speech shorter than its slot is padded, not slowed down.** The contract said
  fit with `atempo`; an `atempo` below 1.0 stretches every vowel to fill time
  the speaker did not use, which sounds broken, while a pause sounds like a
  pause. Timing is identical either way, so the deviation costs nothing. Only
  speed-ups are ever applied.

**A segment outside `0.75–1.35` is reported, not clamped.** Clamping would keep
the segment listenable and put it out of sync with the picture from there
onwards — trading the failure you can hear for the one you cannot. The warning
is keyed to the rate the segment *needed*, so a padded segment that covers 42 %
of its slot is reported too: that usually means the translation dropped
something.

### Voices

Eight presets, and only eight:

`baotrang`, `giahuy`, `hamy`, `huuduc`, `kimoanh`, `maichi`, `quangminh`,
`tiendat` — `GET /voices` returns them without loading the model.

ZeroTTS 0.1.2 still cannot build a voice from a reference clip; the voice
encoder is unpublished. So `voice` is an enum, validated before a job is
created rather than 40 segments into one. If "selectable voice" ever means
*your own voice*, it needs `.npz` latents from zeroweight.ai, which then drop
in with no code change.

### Text normalisation is a separate call

`synthesize()` does not normalise. `zerotts.normalize_vi_text` is applied to
every segment first, which is what turns `31/12/2026` and `1.250.000` into
spoken words. Skip it and dates and numbers are read wrong, in a way you only
catch by listening.

It is applied to the whole line, including any English left in it. That is the
right default for a Vietnamese voiceover — `1999` in a film title should be
read in Vietnamese — but it is a wholesale choice, not a detection.

### It is slower than the benchmark said, and more threads make it worse

ZeroTTS publishes RTF 0.50 on CPU. Measured here it is **1.44** at best, so an
11-minute video is around twenty minutes of synthesis. It is by far the longest
stage in the pipeline, and it is why `progress` exists.

The counter-intuitive part, measured on the same three segments back to back:

| `TTS_THREADS` | RTF | per segment |
|---|---|---|
| 4 | 1.44 | 11.0 s |
| 8 | 2.05 | 15.8 s |
| 12 (one per core) | 3.24 | 24.4 s |

**More threads is 2.2x slower.** The graph is small enough that synchronising
across cores costs more than the parallelism buys. The default here was "one
per core" until this was measured, which made the whole stage take twice as
long as it needed to; ZeroTTS ships `intra_op_num_threads=4` and was right.

### Concurrency comes from sessions, not from threads

Because threads make one session slower, the way to use more of the box is to
run more sessions. `TTS_WORKERS` sets how many ONNX sessions synthesise at the
same time; each one gets `TTS_THREADS` threads.

| Sessions x threads | RTF | Peak resident |
|---|---|---|
| 1 x 4 (the old default) | 1.34 | 4.2 GB |
| **2 x 2 (shipped)** | **1.16** | 6.2 GB |
| 3 x 2 | 1.12 | 8.2 GB |

Two sessions took the voice stage from **22m16s to 18m50s** on the reference
run, 15.6 % off the longest stage in the pipeline. Three reach RTF 1.12 but do
not fit beside the 5.1 GB the render stage peaks at on this host, so the third
session is left on the table rather than traded for an out-of-memory kill.

Generation and placement stay separate: the pool only decides who synthesises
which segment. Every piece is still written to the track at
`round(start_s * 48000)` from its own Whisper start time, so the order segments
come back in cannot move one of them. `tests/test_voice_pool.py` pins that.

Numbers in `reports/tts-spike.md` and `reports/benchmark-after.md`.

## The render half

### `POST /render/jobs`

```bash
curl -X POST http://127.0.0.1:8003/render/jobs \
  -H "Content-Type: application/json" \
  -d '{"video_id": "3gi_15UH9fQ", "preset": "bi-mat-bi-an"}'
```

`only_chunk: 0` renders one chunk instead of all of them — for trying a preset
change against a four-minute clip rather than a forty-minute video.

Reads the chunk rows from the database rather than taking them in the body, for
the same reason chunking reads segments off the volume: the table is the only
place that knows what has already been rendered.

Per chunk it writes `chunks/<NNN>/final.mp4` plus the `subs.ass` it burned, and
joins them into `processed/final.mp4` by stream copy.

**A render fails as a whole if any one chunk failed.** The chunks that did
render keep their rows and their files, so the retry is cheap — but a partly
rendered video that reported success is one that gets uploaded with a hole in
it.

### Presets

`data/presets/<name>.json`, with **every coordinate normalised 0–1**. A preset
written against a 1280×720 source works unchanged on a 1920×1080 one; pixel
coordinates would keep working right up until a source channel changed
resolution and then silently mis-place a blur, which is exactly the failure
nobody notices until it is public.

| Key | Measured against | Notes |
|---|---|---|
| `canvas` | — | Output pixels. `1080×1920` for vertical. |
| `background` | — | File in `presets/backgrounds/`, scaled to the canvas. |
| `video_rect` | the canvas | The **slot**. The source is fitted inside it and centred, never stretched to it. |
| `blur_regions` | the **source frame** | A blur is placed against the thing it hides. Clamped to the frame, and dropped if it falls outside. |
| `logo` | the canvas | `w` only; the height follows the image's aspect ratio. |
| `watermark`, `caption_top`, `caption_bottom` | the canvas | Drawn only when there is text for them. F8 fills the caption boxes from the chunk's `hook` and `caption`. |
| `subtitle` | the canvas | `y` from the top, like every other coordinate. |
| `encode` | — | `crf` and `abr`. The encoder is chosen by trying, not by this file. |

### Subtitles are burned, in ASS

ASS rather than SRT because the preset carries a face, a size, a colour and an
outline and SRT carries none of them — each would have to be restated as an
ffmpeg flag and would then disagree with the preset.

`PlayResX/PlayResY` are the **canvas**, so libass scales the whole script and a
size of 46 means 46 canvas pixels on any source. A segment too long for
`max_lines` becomes two timed cues rather than losing the words the voice is
speaking.

### The encoder is chosen by trying it

`ffmpeg -encoders` lists `h264_nvenc` whenever the binary was built with it,
which says nothing about whether this container can reach a GPU. A one-frame
encode is the only answer that is not a guess; the result is in the job as
`encoder`. This service has no GPU reservation, so it is `libx264` in the
current composition — the GPU stays with Whisper.

## Running the tests

Real ffmpeg, real model, real socket — inside the container:

```bash
docker compose exec render-service python -m pytest tests/ -v
```

`tests/test_timing.py`, `test_preset.py` and `test_subs.py` are pure arithmetic
and finish in under a second. `test_voice.py` and `test_render.py` talk to the
running uvicorn process rather than `TestClient`, which runs background work
inside the request it was started from — a 202 measured through it would look
instant however the endpoint was written. They run on a 19-second video;
`test_render.py` also builds a 720p and a 1080p copy of it and compares where
the picture landed in each finished frame.

**The whole compose stack has to be up.** A session fixture ingests,
transcribes, translates and voices the fixture video by calling the services
that own those stages, rather than assuming somebody left the files behind.
That is not tidiness: `media-service`'s own suite deletes this video's
directory and its `videos` row on purpose, so these tests passed only while
they happened to run first.
