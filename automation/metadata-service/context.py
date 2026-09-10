"""Load the authoritative Vietnamese transcript and chunk context."""

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from errors import MetadataError
from shared import pipeline_db


DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))


@dataclass(frozen=True)
class ChunkContext:
    index: int
    name: str
    text: str


@dataclass(frozen=True)
class GenerationContext:
    video_id: str
    title: str
    transcript: str
    transcript_hash: str
    chunks: tuple[ChunkContext, ...]


def load(video_id: str) -> GenerationContext:
    with pipeline_db.connect() as db:
        video = db.execute(
            "SELECT title FROM videos WHERE video_id = ?", (video_id,)
        ).fetchone()
    if video is None:
        raise MetadataError("video_not_found", "The source video does not exist.", retryable=False)

    directory = DATA_DIR / video_id
    translated = directory / "transcript.vi.json"
    original = directory / "transcript.json"
    path = translated if translated.is_file() else original
    if not path.is_file():
        raise MetadataError("transcript_missing", "The transcript is not ready.", retryable=False)

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MetadataError("transcript_invalid", "The transcript cannot be read.", retryable=False) from exc

    if path == original and str(payload.get("language") or "").lower() != "vi":
        raise MetadataError(
            "translation_missing",
            "The Vietnamese translation is not ready.",
            retryable=False,
        )

    transcript = str(payload.get("transcript") or "").strip()
    if not transcript:
        transcript = " ".join(
            str(segment.get("text") or "").strip()
            for segment in payload.get("segments") or []
        ).strip()
    if not transcript:
        raise MetadataError("transcript_empty", "The transcript contains no text.", retryable=False)

    rows = pipeline_db.chunks_for(video_id)
    chunks = tuple(
        ChunkContext(index=int(row["idx"]), name=str(row["name"]), text=str(row["text"]).strip())
        for row in rows
    )
    if not chunks or any(not chunk.text for chunk in chunks):
        raise MetadataError("chunks_missing", "Balanced chunks are not ready.", retryable=False)
    if any(
        chunk.index != expected_index or chunk.name != f"part_{expected_index + 1}"
        for expected_index, chunk in enumerate(chunks)
    ):
        raise MetadataError(
            "chunks_invalid",
            "Chunk identities must be contiguous and named part_1 through part_N.",
            retryable=False,
        )

    identity = json.dumps(
        {
            "transcript": transcript,
            "chunks": [
                {"index": chunk.index, "name": chunk.name, "text": chunk.text}
                for chunk in chunks
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return GenerationContext(
        video_id=video_id,
        title=str(video["title"] or video_id),
        transcript=transcript,
        transcript_hash=hashlib.sha256(identity).hexdigest(),
        chunks=chunks,
    )
