-- Video re-up pipeline state. See features/video-reup-pipeline/contracts.md.
--
-- WAL, because n8n and several services read this file at once while one
-- writes. Without it a reader blocks a writer and a render stalls behind a
-- status poll.
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- One row per source video. `stage` only ever moves forward, which is what
-- makes a retry cheap: a video that fails at render resumes from
-- 'transcribed' instead of going back to YouTube.
CREATE TABLE IF NOT EXISTS videos (
    video_id    TEXT PRIMARY KEY,
    source_url  TEXT NOT NULL,
    title       TEXT,
    duration_s  REAL,
    width       INTEGER,
    height      INTEGER,
    source_hash TEXT,
    validation_status TEXT NOT NULL DEFAULT 'pending',
    validation_error_code TEXT,
    source_lang TEXT,
    preset      TEXT,
    stage       TEXT NOT NULL DEFAULT 'ingested'
                CHECK (stage IN ('ingested', 'transcribed', 'translated',
                                 'chunked', 'rendered', 'captioned', 'ready')),
    error       TEXT,
    raw_evicted INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- One row per chunk. A chunk is the upload unit, so this table is the upload
-- queue and carries its own caption — not the parent video's.
CREATE TABLE IF NOT EXISTS chunks (
    video_id        TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    idx             INTEGER NOT NULL,
    name            TEXT NOT NULL,
    start_s         REAL NOT NULL,
    end_s           REAL NOT NULL,
    -- Stored, not derived: duration is what decides whether a chunk is
    -- publishable, and the original chunking script never recorded it.
    duration_s      REAL NOT NULL,
    boundary_shift_s REAL NOT NULL DEFAULT 0,
    text            TEXT NOT NULL,
    hook            TEXT,
    caption         TEXT,
    hashtags_json   TEXT,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'rendered', 'failed')),
    final_path      TEXT,
    ready_to_upload INTEGER NOT NULL DEFAULT 0,
    sheet_row       INTEGER,
    PRIMARY KEY (video_id, idx)
);

-- Async work. Every job MUST reach a terminal state and fire its callback,
-- including on failure: a job that dies quietly parks an n8n execution
-- forever, and nobody notices until they go looking.
CREATE TABLE IF NOT EXISTS jobs (
    job_id       TEXT PRIMARY KEY,
    video_id     TEXT NOT NULL,
    kind         TEXT NOT NULL
                 CHECK (kind IN ('ingest', 'transcribe', 'translate', 'voice',
                                'render', 'metadata', 'media_revision')),
    state        TEXT NOT NULL DEFAULT 'queued'
                 CHECK (state IN ('queued', 'running', 'done', 'failed')),
    progress     REAL NOT NULL DEFAULT 0,
    -- The finished payload, as JSON. Kept here as well as sent in the callback
    -- so a lost callback is recoverable by polling instead of by re-running the
    -- work — an ingest costs minutes, a SELECT costs nothing.
    result       TEXT,
    error        TEXT,
    warnings     TEXT,
    callback_url TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_videos_stage   ON videos (stage);
CREATE INDEX IF NOT EXISTS idx_chunks_ready   ON chunks (ready_to_upload, status);
CREATE INDEX IF NOT EXISTS idx_jobs_state     ON jobs (state, kind);
CREATE INDEX IF NOT EXISTS idx_jobs_video     ON jobs (video_id);

-- One deterministic metadata revision per transcript/prompt/model combination.
-- Items are selected independently so a bad chunk does not regenerate the
-- successful YouTube result or its sibling chunks.
CREATE TABLE IF NOT EXISTS metadata_revisions (
    revision_id      TEXT PRIMARY KEY,
    video_id         TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    revision_number  INTEGER NOT NULL,
    generation_key   TEXT NOT NULL UNIQUE,
    transcript_hash  TEXT NOT NULL,
    prompt_version   TEXT NOT NULL,
    schema_version   TEXT NOT NULL,
    model             TEXT NOT NULL,
    state             TEXT NOT NULL DEFAULT 'pending'
                      CHECK (state IN ('pending', 'generating', 'selected',
                                       'needs_action', 'stale')),
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (video_id, revision_number)
);

CREATE TABLE IF NOT EXISTS metadata_items (
    revision_id   TEXT NOT NULL REFERENCES metadata_revisions(revision_id) ON DELETE CASCADE,
    item_key      TEXT NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('youtube', 'chunk')),
    chunk_idx     INTEGER,
    state         TEXT NOT NULL DEFAULT 'pending'
                  CHECK (state IN ('pending', 'generating', 'selected', 'needs_action')),
    selected_json TEXT,
    response_id   TEXT,
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (revision_id, item_key)
);

CREATE TABLE IF NOT EXISTS metadata_attempts (
    attempt_id     TEXT PRIMARY KEY,
    revision_id    TEXT NOT NULL,
    item_key       TEXT NOT NULL,
    attempt_number INTEGER NOT NULL,
    state          TEXT NOT NULL CHECK (state IN ('running', 'selected', 'failed')),
    model          TEXT NOT NULL,
    response_model TEXT,
    response_id    TEXT,
    input_tokens   INTEGER,
    output_tokens  INTEGER,
    total_tokens   INTEGER,
    error_code     TEXT,
    error           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at     TEXT,
    UNIQUE (revision_id, item_key, attempt_number),
    FOREIGN KEY (revision_id, item_key)
        REFERENCES metadata_items(revision_id, item_key) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_metadata_revision_video
    ON metadata_revisions (video_id, revision_number);
CREATE INDEX IF NOT EXISTS idx_metadata_item_state
    ON metadata_items (revision_id, state);

-- Videos whose raw.mp4 can be reclaimed: rendered, older than 7 days, not
-- already evicted. This is the retention sweep's whole query.
CREATE VIEW IF NOT EXISTS evictable_raw AS
    SELECT video_id, updated_at
      FROM videos
     WHERE stage IN ('rendered', 'captioned', 'ready')
       AND raw_evicted = 0
       AND updated_at < datetime('now', '-7 days');
