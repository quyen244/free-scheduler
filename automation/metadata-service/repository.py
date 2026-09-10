"""Durable metadata revisions, per-item selections, and attempt provenance."""

import hashlib
import json
import uuid
from datetime import datetime, timezone

from context import GenerationContext
from schemas import SCHEMA_VERSION
from shared import pipeline_db


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def generation_key(context: GenerationContext, prompt_version: str, model: str) -> str:
    value = "\0".join(
        [context.video_id, context.transcript_hash, prompt_version, SCHEMA_VERSION, model]
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def ensure_revision(context: GenerationContext, prompt_version: str, model: str) -> dict:
    key = generation_key(context, prompt_version, model)
    with pipeline_db.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            existing = db.execute(
                "SELECT * FROM metadata_revisions WHERE generation_key = ?", (key,)
            ).fetchone()
            if existing is not None:
                db.execute("COMMIT")
                return dict(existing)

            number = int(
                db.execute(
                    "SELECT COALESCE(MAX(revision_number), 0) + 1 FROM metadata_revisions WHERE video_id = ?",
                    (context.video_id,),
                ).fetchone()[0]
            )
            revision_id = uuid.uuid4().hex
            db.execute(
                """
                UPDATE metadata_revisions
                   SET state = 'stale', updated_at = ?
                 WHERE video_id = ? AND state != 'stale'
                """,
                (_now(), context.video_id),
            )
            # Every generated chunk result contains visual text. Until the new
            # revision selects that text, an earlier render is no longer valid.
            db.execute(
                """
                UPDATE chunks
                   SET hook = NULL, caption = NULL, hashtags_json = NULL,
                       status = 'pending', final_path = NULL, ready_to_upload = 0
                 WHERE video_id = ?
                """,
                (context.video_id,),
            )
            db.execute(
                """
                INSERT INTO metadata_revisions (
                    revision_id, video_id, revision_number, generation_key,
                    transcript_hash, prompt_version, schema_version, model,
                    state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    revision_id,
                    context.video_id,
                    number,
                    key,
                    context.transcript_hash,
                    prompt_version,
                    SCHEMA_VERSION,
                    model,
                    _now(),
                    _now(),
                ),
            )
            items = [(revision_id, "youtube", "youtube", None)] + [
                (revision_id, chunk.name, "chunk", chunk.index) for chunk in context.chunks
            ]
            db.executemany(
                "INSERT INTO metadata_items (revision_id, item_key, kind, chunk_idx) VALUES (?, ?, ?, ?)",
                items,
            )
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
    return get_revision(revision_id)


def get_revision(revision_id: str) -> dict:
    with pipeline_db.connect() as db:
        row = db.execute(
            "SELECT * FROM metadata_revisions WHERE revision_id = ?", (revision_id,)
        ).fetchone()
    if row is None:
        raise KeyError(revision_id)
    return dict(row)


def selected_item(revision_id: str, item_key: str) -> dict | None:
    with pipeline_db.connect() as db:
        row = db.execute(
            "SELECT selected_json FROM metadata_items WHERE revision_id = ? AND item_key = ? AND state = 'selected'",
            (revision_id, item_key),
        ).fetchone()
    return json.loads(row["selected_json"]) if row and row["selected_json"] else None


def start_attempt(revision_id: str, item_key: str, model: str) -> tuple[str, int]:
    with pipeline_db.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            number = int(
                db.execute(
                    "SELECT COALESCE(MAX(attempt_number), 0) + 1 FROM metadata_attempts WHERE revision_id = ? AND item_key = ?",
                    (revision_id, item_key),
                ).fetchone()[0]
            )
            attempt_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO metadata_attempts (attempt_id, revision_id, item_key, attempt_number, state, model) VALUES (?, ?, ?, ?, 'running', ?)",
                (attempt_id, revision_id, item_key, number, model),
            )
            db.execute(
                "UPDATE metadata_items SET state = 'generating', updated_at = ? WHERE revision_id = ? AND item_key = ?",
                (_now(), revision_id, item_key),
            )
            db.execute(
                "UPDATE metadata_revisions SET state = 'generating', updated_at = ? WHERE revision_id = ?",
                (_now(), revision_id),
            )
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
    return attempt_id, number


def select_attempt(revision_id: str, item_key: str, attempt_id: str, generated) -> None:
    value = generated.value.model_dump_json()
    with pipeline_db.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute(
                """
                UPDATE metadata_attempts
                   SET state = 'selected', response_model = ?, response_id = ?, input_tokens = ?,
                       output_tokens = ?, total_tokens = ?, finished_at = ?
                 WHERE attempt_id = ?
                """,
                (
                    generated.model,
                    generated.response_id,
                    generated.input_tokens,
                    generated.output_tokens,
                    generated.total_tokens,
                    _now(),
                    attempt_id,
                ),
            )
            db.execute(
                """
                UPDATE metadata_items
                   SET state = 'selected', selected_json = ?, response_id = ?, updated_at = ?
                 WHERE revision_id = ? AND item_key = ?
                """,
                (value, generated.response_id, _now(), revision_id, item_key),
            )
            item = db.execute(
                """
                SELECT mr.video_id, mi.kind, mi.chunk_idx
                  FROM metadata_items mi
                  JOIN metadata_revisions mr USING (revision_id)
                 WHERE mi.revision_id = ? AND mi.item_key = ?
                """,
                (revision_id, item_key),
            ).fetchone()
            if item is not None and item["kind"] == "chunk":
                selected = json.loads(value)
                db.execute(
                    """
                    UPDATE chunks
                       SET hook = ?, caption = ?
                     WHERE video_id = ? AND idx = ?
                    """,
                    (
                        selected["hook"],
                        selected["visual_caption"],
                        item["video_id"],
                        item["chunk_idx"],
                    ),
                )
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise


def fail_attempt(
    revision_id: str,
    item_key: str,
    attempt_id: str,
    *,
    error_code: str,
    error: str,
    final: bool,
    response_id: str | None = None,
    response_model: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    total_tokens: int | None = None,
) -> None:
    with pipeline_db.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute(
                """
                UPDATE metadata_attempts
                   SET state = 'failed', response_id = ?, response_model = ?,
                       input_tokens = ?, output_tokens = ?, total_tokens = ?,
                       error_code = ?, error = ?, finished_at = ?
                 WHERE attempt_id = ?
                """,
                (
                    response_id,
                    response_model,
                    input_tokens,
                    output_tokens,
                    total_tokens,
                    error_code,
                    error,
                    _now(),
                    attempt_id,
                ),
            )
            db.execute(
                "UPDATE metadata_items SET state = ?, updated_at = ? WHERE revision_id = ? AND item_key = ?",
                ("needs_action" if final else "pending", _now(), revision_id, item_key),
            )
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise


def finalize_revision(revision_id: str) -> dict:
    with pipeline_db.connect() as db:
        rows = db.execute(
            "SELECT item_key, state FROM metadata_items WHERE revision_id = ? ORDER BY item_key",
            (revision_id,),
        ).fetchall()
        state = "selected" if rows and all(row["state"] == "selected" for row in rows) else "needs_action"
        db.execute(
            "UPDATE metadata_revisions SET state = ?, updated_at = ? WHERE revision_id = ?",
            (state, _now(), revision_id),
        )
    return {"revision_id": revision_id, "state": state, "items": [dict(row) for row in rows]}


def attempts_for(revision_id: str) -> list[dict]:
    with pipeline_db.connect() as db:
        rows = db.execute(
            "SELECT * FROM metadata_attempts WHERE revision_id = ? ORDER BY item_key, attempt_number",
            (revision_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def revision_bundle(revision_id: str) -> dict:
    """Return selected outputs and safe provenance; never prompts or credentials."""
    with pipeline_db.connect() as db:
        revision = db.execute(
            "SELECT * FROM metadata_revisions WHERE revision_id = ?",
            (revision_id,),
        ).fetchone()
        if revision is None:
            raise KeyError(revision_id)
        items = db.execute(
            """
            SELECT mi.item_key, mi.kind, mi.chunk_idx, mi.state,
                   mi.selected_json, mi.response_id, mi.updated_at,
                   ma.response_model, ma.input_tokens, ma.output_tokens,
                   ma.total_tokens
              FROM metadata_items mi
              LEFT JOIN metadata_attempts ma
                ON ma.revision_id = mi.revision_id
               AND ma.item_key = mi.item_key
               AND ma.state = 'selected'
             WHERE mi.revision_id = ?
             ORDER BY CASE mi.kind WHEN 'youtube' THEN 0 ELSE 1 END, mi.chunk_idx
            """,
            (revision_id,),
        ).fetchall()
        attempts = db.execute(
            """
            SELECT attempt_id, item_key, attempt_number, state, model,
                   response_model, response_id, input_tokens, output_tokens,
                   total_tokens, error_code, error, created_at, finished_at
              FROM metadata_attempts
             WHERE revision_id = ?
             ORDER BY item_key, attempt_number
            """,
            (revision_id,),
        ).fetchall()
        usage = db.execute(
            """
            SELECT COALESCE(SUM(input_tokens), 0) AS input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS output_tokens,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COALESCE(SUM(CASE WHEN state = 'selected' THEN 1 ELSE 0 END), 0)
                       AS selected_attempts,
                   COALESCE(SUM(CASE WHEN state = 'failed' THEN 1 ELSE 0 END), 0)
                       AS failed_attempts
              FROM metadata_attempts
             WHERE revision_id = ?
            """,
            (revision_id,),
        ).fetchone()
    outputs = []
    for row in items:
        item = dict(row)
        selected_json = item.pop("selected_json")
        item["selected"] = json.loads(selected_json) if selected_json else None
        outputs.append(item)
    return {
        "revision": dict(revision),
        "items": outputs,
        "attempts": [dict(row) for row in attempts],
        "usage": dict(usage),
    }


def abandon_running_attempts(video_id: str) -> None:
    """Close attempt rows left running when the service process disappeared."""
    with pipeline_db.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            revision_ids = [
                row["revision_id"]
                for row in db.execute(
                    "SELECT revision_id FROM metadata_revisions WHERE video_id = ?",
                    (video_id,),
                ).fetchall()
            ]
            for revision_id in revision_ids:
                db.execute(
                    """
                    UPDATE metadata_attempts
                       SET state = 'failed', error_code = 'service_restarted',
                           error = 'Metadata service restarted during generation.',
                           finished_at = ?
                     WHERE revision_id = ? AND state = 'running'
                    """,
                    (_now(), revision_id),
                )
                db.execute(
                    """
                    UPDATE metadata_items
                       SET state = 'pending', updated_at = ?
                     WHERE revision_id = ? AND state = 'generating'
                    """,
                    (_now(), revision_id),
                )
                db.execute(
                    """
                    UPDATE metadata_revisions
                       SET state = 'needs_action', updated_at = ?
                     WHERE revision_id = ? AND state = 'generating'
                    """,
                    (_now(), revision_id),
                )
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
