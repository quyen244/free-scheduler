import json
import re
from dataclasses import dataclass
from pathlib import Path

from config import settings
from errors import InvalidVideoIdError, MediaNotFoundError, TranscriptNotFoundError

AUDIO_NAME = "audio.wav"
META_NAME = "meta.json"
TRANSCRIPT_NAME = "transcript.json"
TRANSLATED_NAME = "transcript.vi.json"

# A YouTube id is exactly 11 characters from a known alphabet. The id arrives
# from a request and is used to build a filesystem path, so it is checked
# against that shape before it is joined to anything.
_VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")


@dataclass(frozen=True)
class Media:
    video_id: str
    title: str
    duration_s: float
    audio_path: Path


def load(video_id: str) -> Media:
    if not _VIDEO_ID_PATTERN.fullmatch(video_id):
        raise InvalidVideoIdError(f"not a valid video id: {video_id!r}")

    directory = settings.data_dir / video_id
    audio = directory / AUDIO_NAME
    meta = directory / META_NAME

    if not audio.is_file():
        # Deliberately does not fall back to downloading. One service owns
        # acquisition; a fallback here would mean two things fetching from
        # YouTube and would quietly undo that.
        raise MediaNotFoundError(
            f"no audio for {video_id!r} — POST the url to the media service's "
            f"/download first"
        )

    record = json.loads(meta.read_text(encoding="utf-8")) if meta.is_file() else {}
    return Media(
        video_id=video_id,
        title=str(record.get("title") or video_id),
        duration_s=float(record.get("duration_s") or 0.0),
        audio_path=audio,
    )


def save_transcript(video_id: str, payload: dict) -> Path:
    """Write the transcript beside the audio it came from.

    Via a partial name, like every other write to this volume: chunking reads
    this file, and a half-written one parses as either a crash or — worse — a
    short segment list that silently drops the end of the video.
    """
    path = settings.data_dir / video_id / TRANSCRIPT_NAME
    partial = path.with_name(f"{TRANSCRIPT_NAME}.part")
    partial.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    partial.replace(path)
    return path


def load_transcript(video_id: str) -> dict:
    """The authoritative transcript for a video.

    The Vietnamese one wins when it exists. Chunk text becomes the caption, the
    subtitles and the TTS script, so chunking an English transcript that has
    already been translated would quietly ship an English video. The timings
    are identical either way — translate-service copies them rather than
    re-deriving them — so the chunk boundaries do not depend on which file this
    returns.
    """
    if not _VIDEO_ID_PATTERN.fullmatch(video_id):
        raise InvalidVideoIdError(f"not a valid video id: {video_id!r}")

    directory = settings.data_dir / video_id
    for name in (TRANSLATED_NAME, TRANSCRIPT_NAME):
        path = directory / name
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))

    raise TranscriptNotFoundError(
        f"no transcript for {video_id!r} — POST it to /transcribe first"
    )
