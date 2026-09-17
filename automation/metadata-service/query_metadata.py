"""Print the selected social-post metadata for one pipeline video.

The command is deliberately read-only: it opens ``pipeline.db`` with SQLite's
``mode=ro`` URI and never calls the schema/migration helper.  The selected
metadata revision, rather than the compatibility columns on ``chunks``, is the
source of truth for platform captions and hashtags.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any


class MetadataQueryError(Exception):
    """Expected operator-facing failure while reading selected metadata."""


def default_db_path() -> Path:
    """Use the Compose data mount when available, otherwise the repository DB."""
    data_dir = os.environ.get("DATA_DIR")
    if data_dir:
        return Path(data_dir) / "pipeline.db"
    return Path(__file__).resolve().parents[1] / "data" / "pipeline.db"


def _connect_read_only(db_path: Path) -> sqlite3.Connection:
    resolved = db_path.resolve(strict=False)
    if not resolved.is_file():
        raise MetadataQueryError(f"pipeline database was not found: {resolved}")
    connection = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def selected_metadata(db_path: Path, video_id: str) -> dict[str, Any]:
    """Return the newest complete selected revision for ``video_id``.

    ``chunks.hook`` and ``chunks.caption`` are intentionally not read: those
    are renderer compatibility copies and cannot represent per-platform copy
    or metadata revision provenance.
    """
    with _connect_read_only(db_path) as db:
        video = db.execute(
            """
            SELECT video_id, title, duration_s, stage, created_at, updated_at
              FROM videos
             WHERE video_id = ?
            """,
            (video_id,),
        ).fetchone()
        if video is None:
            raise MetadataQueryError(f"video was not found: {video_id}")

        revision = db.execute(
            """
            SELECT revision_id, revision_number, prompt_version, schema_version,
                   model, created_at, updated_at
              FROM metadata_revisions
             WHERE video_id = ? AND state = 'selected'
             ORDER BY revision_number DESC
             LIMIT 1
            """,
            (video_id,),
        ).fetchone()
        if revision is None:
            raise MetadataQueryError(
                f"video {video_id} has no selected metadata revision"
            )

        items = db.execute(
            """
            SELECT item_key, kind, chunk_idx, selected_json
              FROM metadata_items
             WHERE revision_id = ? AND state = 'selected'
             ORDER BY CASE kind WHEN 'youtube' THEN 0 ELSE 1 END, chunk_idx
            """,
            (revision["revision_id"],),
        ).fetchall()
        chunks = db.execute(
            """
            SELECT idx, name, start_s, end_s, duration_s
              FROM chunks
             WHERE video_id = ?
             ORDER BY idx
            """,
            (video_id,),
        ).fetchall()

    parsed_items: dict[tuple[str, int | None], dict[str, Any]] = {}
    for item in items:
        raw = item["selected_json"]
        try:
            selected = json.loads(raw) if raw else None
        except json.JSONDecodeError as exc:
            raise MetadataQueryError(
                f"selected metadata item {item['item_key']} contains invalid JSON"
            ) from exc
        if not isinstance(selected, dict):
            raise MetadataQueryError(
                f"selected metadata item {item['item_key']} is missing its JSON object"
            )
        parsed_items[(str(item["kind"]), item["chunk_idx"])] = selected

    youtube = parsed_items.get(("youtube", None))
    if youtube is None:
        raise MetadataQueryError(
            f"metadata revision {revision['revision_number']} has no selected YouTube item"
        )

    result_chunks: list[dict[str, Any]] = []
    for chunk in chunks:
        content = parsed_items.get(("chunk", chunk["idx"]))
        if content is None:
            raise MetadataQueryError(
                f"metadata revision {revision['revision_number']} is missing {chunk['name']}"
            )
        result_chunks.append(
            {
                "part": str(chunk["name"]),
                "index": int(chunk["idx"]) + 1,
                "start_s": float(chunk["start_s"]),
                "end_s": float(chunk["end_s"]),
                "duration_s": float(chunk["duration_s"]),
                "hook": content.get("hook"),
                "visual_caption": content.get("visual_caption"),
                "facebook": content.get("facebook"),
                "tiktok": content.get("tiktok"),
            }
        )
    final = {
        "schema_version": "metadata-export.v1",
        "video": dict(video),
        "metadata_revision": dict(revision),
        "youtube": youtube,
        "chunks": result_chunks,
    }
    path_metadata = Path(__file__).parent.parent / 'data' / str(video_id) / f'metadata_{video_id}.json'
    try:
        with open(path_metadata, 'w', encoding='utf-8') as f:
            json.dump(final , f , skipkeys=True , ensure_ascii=False)
    except Exception as e:
        print(f'error duting dump metadata for video id {video_id}: {e}')
        
    return final


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read selected social-post metadata for one pipeline video."
    )
    parser.add_argument("--id", required=True, help="Pipeline/YouTube video ID")
    parser.add_argument(
        "--db",
        type=Path,
        default=default_db_path(),
        help="Path to pipeline.db (default: Compose DATA_DIR or automation/data/pipeline.db)",
    )
    parser.add_argument(
        "--compact", action="store_true", help="Emit compact one-line JSON"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        payload = selected_metadata(args.db, args.id)
    except MetadataQueryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print('dump metadata successfully !')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
