"""Shared access to ``data/pipeline.db``.

Bind-mounted into every service at ``/app/shared``, because the ``jobs`` table
has no single owner: whichever service does the work writes its own job row —
media-service for ingest, the transcript service for transcribe, render for
render. One schema in one place beats three drifting copies.

The ``videos`` and ``chunks`` tables do have owners. See
``features/video-reup-pipeline/contracts.md``.
"""

import json
import logging
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DB_PATH = Path(os.environ.get("PIPELINE_DB", str(DATA_DIR / "pipeline.db")))
SCHEMA_PATH = DATA_DIR / "schema.sql"

JOB_KINDS = ("ingest", "transcribe", "translate", "voice", "render")

# A video only ever moves forward through these. See contracts.md.
STAGE_ORDER = (
    "ingested", "transcribed", "translated",
    "chunked", "rendered", "captioned", "ready",
)
_STAGE_RANK = "," + ",".join(STAGE_ORDER) + ","
TERMINAL_STATES = ("done", "failed")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Open a short-lived autocommit connection.

    Short-lived on purpose: several containers write this file, and a
    connection held open across a two-minute download would keep a writer lock
    for two minutes. Take it, write, drop it.
    """
    conn = sqlite3.connect(DB_PATH, timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        # Losing a lock race here means a status poll fails, which the caller
        # reads as a dead job. Wait instead.
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
    finally:
        conn.close()


def init() -> None:
    """Apply ``schema.sql``. Idempotent — every statement is IF NOT EXISTS."""
    if not SCHEMA_PATH.is_file():
        logger.warning("no schema at %s; assuming the database is already set up", SCHEMA_PATH)
        return
    with connect() as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    _widen_job_kinds()
    _add_chunk_contract_columns()
    _add_source_validation_columns()


def _add_chunk_contract_columns() -> None:
    """Migrate installed SQLite databases to the named balanced-chunk contract."""
    with connect() as conn:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(chunks)").fetchall()
        }
        if "name" not in columns:
            conn.execute("ALTER TABLE chunks ADD COLUMN name TEXT NOT NULL DEFAULT ''")
        if "boundary_shift_s" not in columns:
            conn.execute(
                "ALTER TABLE chunks ADD COLUMN boundary_shift_s REAL NOT NULL DEFAULT 0"
            )
        conn.execute(
            "UPDATE chunks SET name = 'part_' || (idx + 1) "
            "WHERE name IS NULL OR name = ''"
        )


def _add_source_validation_columns() -> None:
    """Add source identity and validation evidence without replacing user data."""
    definitions = {
        "width": "INTEGER",
        "height": "INTEGER",
        "source_hash": "TEXT",
        "rights_status": "TEXT NOT NULL DEFAULT 'unknown'",
        "rights_evidence": "TEXT",
        "validation_status": "TEXT NOT NULL DEFAULT 'pending'",
        "validation_error_code": "TEXT",
    }
    with connect() as conn:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(videos)").fetchall()
        }
        for name, definition in definitions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE videos ADD COLUMN {name} {definition}")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_videos_source_hash ON videos (source_hash)"
        )


def _widen_job_kinds() -> None:
    """Let the ``jobs`` table accept the kinds this version knows about.

    ``schema.sql`` is declarative and every statement is ``IF NOT EXISTS``, so
    an edit to a CHECK constraint reaches a fresh database and no existing one.
    F6 added a ``voice`` kind, and without this an installed instance answers
    every voice job with a constraint failure inside the worker — a job that is
    born failed.

    SQLite cannot alter a constraint, so this is the standard table rebuild,
    skipped entirely once the constraint already names every kind.
    """
    with connect() as conn:
        definition = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'jobs'"
        ).fetchone()
        if definition is None or all(kind in definition["sql"] for kind in JOB_KINDS):
            return

        logger.info("widening jobs.kind to %s", ", ".join(JOB_KINDS))
        kinds = ", ".join(f"'{kind}'" for kind in JOB_KINDS)
        columns = (
            "job_id, video_id, kind, state, progress, result, error, warnings, "
            "callback_url, created_at, finished_at"
        )
        # Statement by statement rather than executescript: that method commits
        # whatever transaction is open before it runs, which would drop the
        # BEGIN IMMEDIATE below and leave a half-rebuilt table if a step failed.
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                f"""
                CREATE TABLE jobs_rebuilt (
                    job_id       TEXT PRIMARY KEY,
                    video_id     TEXT NOT NULL,
                    kind         TEXT NOT NULL CHECK (kind IN ({kinds})),
                    state        TEXT NOT NULL DEFAULT 'queued'
                                 CHECK (state IN ('queued', 'running', 'done', 'failed')),
                    progress     REAL NOT NULL DEFAULT 0,
                    result       TEXT,
                    error        TEXT,
                    warnings     TEXT,
                    callback_url TEXT,
                    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
                    finished_at  TEXT
                )
                """
            )
            conn.execute(f"INSERT INTO jobs_rebuilt ({columns}) SELECT {columns} FROM jobs")
            conn.execute("DROP TABLE jobs")
            conn.execute("ALTER TABLE jobs_rebuilt RENAME TO jobs")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs (state, kind)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_video ON jobs (video_id)")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def _now() -> str:
    """UTC, in SQLite's own ``datetime('now')`` format.

    The schema defaults use that format, so a mixed-format column would break
    the retention sweep's ``updated_at < datetime('now', '-7 days')``.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# --- videos ---------------------------------------------------------------


def record_ingested(
    video_id: str,
    source_url: str,
    title: str,
    duration_s: float,
    source_hash: str | None = None,
    width: int | None = None,
    height: int | None = None,
    rights_status: str = "unknown",
    rights_evidence: str | None = None,
) -> None:
    """Create or refresh the parent row for a video that now has media on disk.

    Deliberately does **not** write ``stage`` on the conflict path. A video
    already at ``rendered`` that gets re-ingested must not fall back to
    ``ingested`` — the stage only ever moves forward, and that is what makes a
    resume cheap.
    """
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO videos (
                video_id, source_url, title, duration_s, width, height,
                source_hash, rights_status, rights_evidence, validation_status,
                validation_error_code, stage, updated_at
            )
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'valid', NULL, 'ingested', ?)
            ON CONFLICT(video_id) DO UPDATE SET
                     source_url = excluded.source_url,
                     title      = excluded.title,
                     duration_s = excluded.duration_s,
                     width      = excluded.width,
                     height     = excluded.height,
                     source_hash = excluded.source_hash,
                     rights_status = excluded.rights_status,
                     rights_evidence = excluded.rights_evidence,
                     validation_status = 'valid',
                     validation_error_code = NULL,
                     error      = NULL,
                     updated_at = excluded.updated_at
            """,
            (
                video_id,
                source_url,
                title,
                duration_s,
                width,
                height,
                source_hash,
                rights_status,
                rights_evidence,
                _now(),
            ),
        )


def record_validation_failure(
    video_id: str,
    source_url: str,
    title: str,
    rights_status: str,
    rights_evidence: str | None,
    error_code: str,
    error: str,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO videos (
                video_id, source_url, title, rights_status, rights_evidence,
                validation_status, validation_error_code, stage, error, updated_at
            )
                 VALUES (?, ?, ?, ?, ?, 'failed', ?, 'ingested', ?, ?)
            ON CONFLICT(video_id) DO UPDATE SET
                     source_url = excluded.source_url,
                     title = excluded.title,
                     rights_status = excluded.rights_status,
                     rights_evidence = excluded.rights_evidence,
                     validation_status = 'failed',
                     validation_error_code = excluded.validation_error_code,
                     error = excluded.error,
                     updated_at = excluded.updated_at
            """,
            (
                video_id,
                source_url,
                title,
                rights_status,
                rights_evidence,
                error_code,
                error,
                _now(),
            ),
        )


def set_preset(video_id: str, preset: str) -> None:
    """Record which preset a video was rendered with.

    So a resume in F9 re-renders it the way it was rendered the first time,
    rather than with whatever the service's default happens to be that week.
    """
    with connect() as conn:
        conn.execute(
            "UPDATE videos SET preset = ?, updated_at = ? WHERE video_id = ?",
            (preset, _now(), video_id),
        )


def advance_stage(video_id: str, stage: str) -> None:
    """Move a video forward through the stage order. Never backwards.

    Re-running an earlier stage is routine — re-transcribing after a bad model
    choice, re-chunking with a different threshold — and it must not drag a
    video that already rendered back to the beginning. The comparison happens
    in SQL rather than read-then-write, so a concurrent writer cannot act on a
    rank it read a moment ago.
    """
    if stage not in STAGE_ORDER:
        raise ValueError(f"unknown stage {stage!r}")
    with connect() as conn:
        conn.execute(
            """
            UPDATE videos
               SET stage = ?, updated_at = ?
             WHERE video_id = ?
               AND instr(?, ',' || stage || ',') < instr(?, ',' || ? || ',')
            """,
            (stage, _now(), video_id, _STAGE_RANK, _STAGE_RANK, stage),
        )


# --- chunks ---------------------------------------------------------------


def replace_chunks(video_id: str, chunks: list[dict[str, object]]) -> None:
    """Make the `chunks` rows for a video match `chunks` exactly.

    Re-chunking is routine — a different threshold, a re-transcribe after a
    better model — so this is an upsert plus a truncate rather than a delete
    and re-insert. Deleting first would drop the render state of every chunk
    whose boundaries did not actually change, and would briefly leave a video
    with no rows at all for anything reading concurrently.

    Render state is kept only where the boundaries survived. A chunk that now
    covers a different span of the video is a different chunk, so any
    `final.mp4` rendered for the old span is stale and the row goes back to
    `pending`.
    """
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.executemany(
                """
                INSERT INTO chunks (
                    video_id, idx, name, start_s, end_s, duration_s,
                    boundary_shift_s, text
                )
                     VALUES (
                        :video_id, :idx, :name, :start_s, :end_s, :duration_s,
                        :boundary_shift_s, :text
                     )
                ON CONFLICT(video_id, idx) DO UPDATE SET
                         name       = excluded.name,
                         start_s    = excluded.start_s,
                         end_s      = excluded.end_s,
                         duration_s = excluded.duration_s,
                         boundary_shift_s = excluded.boundary_shift_s,
                         text       = excluded.text,
                         status = CASE WHEN chunks.start_s = excluded.start_s
                                        AND chunks.end_s   = excluded.end_s
                                       THEN chunks.status ELSE 'pending' END,
                         final_path = CASE WHEN chunks.start_s = excluded.start_s
                                            AND chunks.end_s   = excluded.end_s
                                           THEN chunks.final_path ELSE NULL END,
                         ready_to_upload = CASE WHEN chunks.start_s = excluded.start_s
                                                 AND chunks.end_s   = excluded.end_s
                                                THEN chunks.ready_to_upload ELSE 0 END
                """,
                [{"video_id": video_id, **chunk} for chunk in chunks],
            )
            conn.execute(
                "DELETE FROM chunks WHERE video_id = ? AND idx >= ?",
                (video_id, len(chunks)),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def chunks_for(video_id: str) -> list[dict[str, object]]:
    """A video's chunks, in order.

    Read from here rather than passed through n8n. Three stages need them, and
    the table is the only place that knows whether a chunk has already been
    rendered.
    """
    with connect() as conn:
        rows = conn.execute(
            "SELECT idx, name, start_s, end_s, duration_s, boundary_shift_s, "
            "text, hook, caption, status, "
            "final_path FROM chunks WHERE video_id = ? ORDER BY idx",
            (video_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def mark_chunk_rendered(video_id: str, idx: int, final_path: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE chunks SET status = 'rendered', final_path = ? "
            "WHERE video_id = ? AND idx = ?",
            (final_path, video_id, idx),
        )


def mark_chunk_failed(video_id: str, idx: int) -> None:
    """Record that this chunk has no usable output.

    Its `final_path` is cleared with it: a path left pointing at the previous
    render is worse than none, because the sheet would list it as ready.
    """
    with connect() as conn:
        conn.execute(
            "UPDATE chunks SET status = 'failed', final_path = NULL, "
            "ready_to_upload = 0 WHERE video_id = ? AND idx = ?",
            (video_id, idx),
        )


# --- jobs -----------------------------------------------------------------


def create_job(video_id: str, kind: str, callback_url: str | None = None) -> str:
    if kind not in JOB_KINDS:
        raise ValueError(f"unknown job kind {kind!r}")
    job_id = uuid.uuid4().hex
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO jobs (job_id, video_id, kind, state, callback_url, created_at)
                 VALUES (?, ?, ?, 'queued', ?, ?)
            """,
            (job_id, video_id, kind, callback_url, _now()),
        )
    return job_id


def mark_running(job_id: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE jobs SET state = 'running' WHERE job_id = ?", (job_id,))


def set_progress(job_id: str, progress: float) -> None:
    """Record how far a long job has got.

    Worth the write for voicing and rendering, which run for tens of minutes
    with an n8n execution parked on them. Without it the only two observable
    states are 'running' and 'finished', so a job that is slow looks exactly
    like a job that is stuck.
    """
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET progress = ? WHERE job_id = ? AND state = 'running'",
            (round(min(max(progress, 0.0), 1.0), 4), job_id),
        )


def finish_job(
    job_id: str,
    state: str,
    result: dict[str, object] | None = None,
    error: str | None = None,
    warnings: list[str] | None = None,
) -> None:
    if state not in TERMINAL_STATES:
        raise ValueError(f"{state!r} is not a terminal state")
    with connect() as conn:
        conn.execute(
            """
            UPDATE jobs
               SET state = ?, progress = ?, result = ?, error = ?, warnings = ?,
                   finished_at = ?
             WHERE job_id = ?
            """,
            (
                state,
                1.0 if state == "done" else 0.0,
                json.dumps(result, ensure_ascii=False) if result is not None else None,
                error,
                json.dumps(warnings, ensure_ascii=False) if warnings else None,
                _now(),
                job_id,
            ),
        )


def unfinished_jobs(kinds: tuple[str, ...]) -> list[dict[str, object]]:
    """Jobs left mid-flight, for the given kinds.

    Scoped by kind because each service reaps only its own work. A service that
    reaped every unfinished row would kill the other services' live jobs every
    time it restarted.
    """
    placeholders = ",".join("?" for _ in kinds)
    with connect() as conn:
        rows = conn.execute(
            "SELECT job_id, video_id FROM jobs "
            f"WHERE state IN ('queued', 'running') AND kind IN ({placeholders})",
            kinds,
        ).fetchall()
    return [dict(row) for row in rows]


def get_job(job_id: str) -> dict[str, object] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    if row is None:
        return None
    job = dict(row)
    job["result"] = json.loads(job["result"]) if job["result"] else None
    job["warnings"] = json.loads(job["warnings"]) if job["warnings"] else None
    return job
