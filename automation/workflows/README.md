# Workflows

n8n workflow JSON, in version control. Mounted read-only into the n8n container
at `/workflows`. The editor is where workflows change; this directory is where
they are recorded, and a two-way sync would only fight itself.

`reup-pipeline.json` is the canonical export of the latest reviewed live
workflow. Files named `f3` through `f7` are historical phase snapshots. Do not
import a phase snapshot over the canonical or live workflow unless rollback to
that exact phase is intentional.

```bash
docker compose exec -T n8n n8n import:workflow --input=/workflows/reup-pipeline.json
```

## One workflow, one id

Every feature file here carries the **same workflow id, `reupPipeline`, and the
same webhook path, `reup-pipeline`.** Importing a feature file updates that one
workflow in place; the files are the record of what the pipeline looked like at
each feature, not separate workflows.

This is not cosmetic. Two workflows claiming one webhook path leaves the loser
silently unregistered — no error at import, no error at activation, and the
path answers `404 Active version not found` naming the *other* workflow.
Deactivating the winner does not hand the path back either: the row in
`webhook_entity` outlives the deactivation when it is done through the CLI
while n8n is running. Clearing it needs a direct delete:

```bash
docker compose exec n8n node -e "
const sqlite3=require('/usr/local/lib/node_modules/n8n/node_modules/sqlite3');
new sqlite3.Database('/home/node/.n8n/database.sqlite')
  .run(\"delete from webhook_entity where workflowId='<stale-id>'\");"
docker compose restart n8n
```

> On Git Bash for Windows, prefix with `MSYS_NO_PATHCONV=1` or the `/workflows`
> argument is rewritten to `C:/Program Files/Git/workflows` before Docker sees
> it.

To go the other way, after editing in the UI:

```bash
docker compose exec n8n n8n export:workflow --id=<id> --pretty --output=/tmp/w.json
docker cp n8n:/tmp/w.json workflows/<name>.json
```

## Historical phase snapshots

The sections below explain how the pipeline grew. They are documentation and
rollback points, not the current import target.

### `f3-ingest-async.json`

`Webhook → Start ingest → Wait for ingest → Ingest ok? → Ingested | Ingest failed`

The async ingest loop. `POST http://localhost:5678/webhook/reup-ingest` with
`{"url": "..."}`; the execution parks on the Wait node and media-service resumes
it when the download ends. F4 continues from the **Ingested** node, which
carries `video_id` — the transcript service never sees a URL.

Two settings in it are load-bearing and easy to lose by rebuilding the workflow
in the UI:

**The Wait node's HTTP method is POST.** It defaults to GET, and a POST callback
to a GET-only resume webhook gets a 404. The service logs a delivered callback,
n8n never resumes, and the execution waits forever for something that already
happened.

**The callback host is rewritten:**

```
{{ 'http://n8n:5678/webhook-waiting/' + $execution.resumeUrl.split('/webhook-waiting/')[1] }}
```

`$execution.resumeUrl` is built from `WEBHOOK_URL`, which is `localhost:5678`
here — and `localhost` inside media-service means media-service. Splitting on
the path rather than replacing the host keeps the execution id **and** the
`?signature=` token that n8n 2.37 appends to resume URLs. Setting `WEBHOOK_URL`
to `http://n8n:5678/` instead would fix the callback and break every
browser-facing webhook URL in the editor.

### `f4-ingest-transcribe-chunk.json`

`Webhook → Start ingest → Wait for ingest → Ingest ok? → Ingested → Transcribe
→ Chunk → Chunked`

F3's workflow with the transcript service wired on. `POST
http://localhost:5678/webhook/reup-pipeline` with `{"url": "..."}`; an
11-minute source that is already ingested completes in about 30 seconds.

**Transcribe is synchronous, unlike ingest.** Transcription runs at roughly
0.05x realtime on the GPU, so the HTTP call is not the bottleneck ingest was —
but n8n's HTTP node still defaults to a 300 s timeout, so the node carries an
explicit 15-minute one. A source long enough to hit that turns this node into
a job + Wait pair, exactly like ingest.

**Chunk takes a `video_id`, not segments.** The service reads
`transcript.json` off the shared volume. To tune the thresholds, add
`max_chunk_s` / `min_chunk_s` to that node's body — no rebuild, which was the
only real argument for keeping chunking in a Code node.

### `f5-translate.json`

`... → Transcribe → Vietnamese? → Chunk`
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;`↳ Start translate → Wait for translation → Translate ok? → Chunk | Translate failed`

The same pipeline with the language branch added. Import it over the F4 file —
same `reupPipeline` id, same `reup-pipeline` path.

**`Vietnamese?` is acceptance criterion 9.** Whisper's detected `language` is
the only input to the decision, which is why `/transcribe` returns it. A `vi`
source goes straight to `Chunk` and the translate service is never called at
all — not called and skipped internally, but never contacted.

**Translate is async, like ingest and unlike transcribe.** 104 segments at
about 1.5 s each is minutes. Same two load-bearing settings as `Wait for
ingest`: the Wait node's HTTP method is POST, and the callback host is
rewritten by splitting `$execution.resumeUrl` on `/webhook-waiting/` rather
than replacing the host.

**`Chunk` reads `$('Transcribe')`, not `$json`.** Two branches arrive at it
carrying different shapes — the direct one holds the transcribe response, the
translated one holds a job callback — and naming the node it wants is what
makes it work from both.

**`Translate failed` exists for misalignment.** A segment list that came back
one short would shift every subtitle after it, and that is only visible two
stages later. It lands here instead of in the `chunks` table.

### `f6-voice.json`

`... → Chunked → Start voice → Wait for voice → Voice ok? → Voiced | Voice failed`

The same pipeline with the Vietnamese voice track added after chunking. Import
it over the F5 file — same `reupPipeline` id, same `reup-pipeline` path.

**This is by far the longest node in the pipeline.** Synthesis runs slower than
real time on CPU: about 20 s per Whisper segment, so an 11-minute video is
roughly 35 minutes parked on `Wait for voice`. Async for the obvious reason,
and the job row carries `progress` so a slow job can be told apart from a stuck
one while it sits there.

**`warnings` is carried onto the `Voiced` node deliberately.** A segment that
needed more than 1.35x to fit its slot is shipped at that rate, not clamped —
clamping would keep it listenable and put it out of sync with the picture from
there onwards. The only defence against an unlistenable segment is that
somebody is told, so the array has to survive to the end of the pipeline where
F8 sends it.

### `f7-render.json`

`... → Voiced → Start render → Wait for render → Render ok? → Rendered | Render failed`

The historical F7 pipeline ends in a vertical render. It is not the current
import target and it does not yet create the required landscape YouTube asset.

**`Start render` names no preset.** It uses the service's `DEFAULT_PRESET`, so
the geometry lives in `data/presets/<name>.json` and not in the workflow. Add a
`preset` field to run one video against another channel's layout, or
`only_chunk` to try a preset change against one clip instead of a whole video.

**`Render ok?` is false if any single chunk failed.** The chunks that did render
keep their rows and their files, so a retry re-does only what is missing — but
a partly rendered video that reported success is one that gets uploaded with a
hole in it.

**`Rendered` is where F8 attaches.** It already carries `processed_path`,
`total_chunks`, `preset` and `warnings` — everything the sheet row and the
Telegram message need except the caption itself.
