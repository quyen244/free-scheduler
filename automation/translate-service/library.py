"""Reading and writing the transcript files on the shared volume."""

import json
import re
from pathlib import Path

from config import settings
from errors import InvalidVideoIdError, TranscriptNotFoundError

TRANSCRIPT_NAME = "transcript.json"
TRANSLATED_NAME = "transcript.vi.json"
BUDGET_REPORT_NAME = "translate-budget.json"

# A YouTube id is exactly 11 characters from a known alphabet. It arrives from
# a request and is joined to a filesystem path, so it is checked first.
_VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")


def video_dir(video_id: str) -> Path:
    if not _VIDEO_ID_PATTERN.fullmatch(video_id):
        raise InvalidVideoIdError(f"not a valid video id: {video_id!r}")
    return settings.data_dir / video_id


def load_transcript(video_id: str) -> dict:
    path = video_dir(video_id) / TRANSCRIPT_NAME
    if not path.is_file():
        raise TranscriptNotFoundError(
            f"no transcript for {video_id!r} — POST it to the transcript "
            f"service's /transcribe first"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def save_translation(video_id: str, payload: dict) -> Path:
    """Write the Vietnamese transcript beside the original.

    Beside it, not over it: the source text is what a mistranslation is
    diagnosed against, and re-transcribing an 11-minute video to get it back
    costs a GPU minute for no reason.

    Written through a partial name and renamed, like every write to this
    volume. Chunking reads this file, and a half-written one is either a parse
    error or — worse — a short segment list that silently drops the end.
    """
    path = video_dir(video_id) / TRANSLATED_NAME
    partial = path.with_name(f"{TRANSLATED_NAME}.part")
    partial.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    partial.replace(path)
    return path


def save_budget_report(
    video_id: str, rows: list[dict], directory: Path | None = None
) -> Path:
    """Write what the second pass decided, segment by segment.

    Its own file rather than a key in the transcript: F4 and F6 parse
    `transcript.vi.json`, and evidence about a run must not be able to break
    the run. An empty list is written too - "nothing needed shortening" is a
    result, and a missing file cannot be told apart from a stage that never ran.

    `directory` exists for the tests. Everything else goes to the volume.
    """
    target = video_dir(video_id) if directory is None else directory
    path = target / BUDGET_REPORT_NAME
    partial = path.with_name(f"{BUDGET_REPORT_NAME}.part")
    payload = {"video_id": video_id, "total": len(rows), "segments": rows}
    try:
        partial.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        partial.replace(path)
    except OSError:
        # Evidence is not the deliverable. A translation that succeeded must not
        # be thrown away because the audit file could not be written.
        partial.unlink(missing_ok=True)
        raise
    return path
