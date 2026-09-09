import json
import logging
import re
import subprocess
from pathlib import Path

import yt_dlp

from config import settings
from errors import AudioExtractionError, DownloadError, InvalidURLError, SourcePolicyError
from shared import pipeline_db
import validation

logger = logging.getLogger(__name__)

_VIDEO_ID_PATTERN = re.compile(
    r"(?:youtube\.com/(?:watch\?v=|shorts/|embed/)|youtu\.be/)([A-Za-z0-9_-]{11})"
)

RAW_NAME = "raw.mp4"
AUDIO_NAME = "audio.wav"
META_NAME = "meta.json"

_PARTIAL_PREFIX = "raw.part"


def extract_video_id(url: str) -> str:
    match = _VIDEO_ID_PATTERN.search(url)
    if not match:
        raise InvalidURLError(f"not a recognizable YouTube URL: {url!r}")
    return match.group(1)


def video_dir(video_id: str) -> Path:
    return settings.data_dir / video_id


def get_or_download(
    url: str,
    rights_status: str,
    rights_evidence: str | None = None,
) -> tuple[dict[str, object], bool]:
    video_id = extract_video_id(url)
    validation.validate_rights(rights_status)
    directory = video_dir(video_id)
    raw = directory / RAW_NAME
    audio = directory / AUDIO_NAME
    meta = directory / META_NAME

    # All three or none. A directory left half-populated by an interrupted run
    # must not read as a cache hit, or the next stage gets a truncated file.
    if raw.is_file() and audio.is_file() and meta.is_file():
        record = json.loads(meta.read_text(encoding="utf-8"))
        record = _validate_and_enrich(
            record, raw, url, video_id, rights_status, rights_evidence
        )
        meta.write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _record_ingested(record)
        return record, True

    directory.mkdir(parents=True, exist_ok=True)
    info = _download_video(url, directory, raw)

    record: dict[str, object] = {
        "video_id": video_id,
        "source_url": url,
        "title": info.get("title") or video_id,
        "duration_s": float(info.get("duration") or 0.0),
    }
    try:
        record = _validate_and_enrich(
            record, raw, url, video_id, rights_status, rights_evidence
        )
    except SourcePolicyError as exc:
        pipeline_db.record_validation_failure(
            video_id=video_id,
            source_url=url,
            title=str(record["title"]),
            rights_status=rights_status,
            rights_evidence=rights_evidence,
            error_code=exc.code,
            error=str(exc),
        )
        raise

    _extract_audio(raw, audio)
    meta.write_text(
        # ensure_ascii=False: titles are routinely Vietnamese or Chinese, and
        # escape sequences make the file unreadable when inspected by hand.
        json.dumps(record, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("downloaded %s (%.1fs): %s", video_id, record["duration_s"], record["title"])
    _record_ingested(record)
    return record, False


def _record_ingested(record: dict[str, object]) -> None:
    """Announce the media to the rest of the pipeline.

    Called on the cache-hit path as well as the download path, so the two
    endpoints in front of this function cannot disagree about what exists. A
    write that only happened on a fresh download would leave every re-run
    invisible to the database.
    """
    pipeline_db.record_ingested(
        video_id=str(record["video_id"]),
        source_url=str(record["source_url"]),
        title=str(record["title"]),
        duration_s=float(record["duration_s"]),  # type: ignore[arg-type]
        source_hash=str(record["source_hash"]),
        width=int(record["width"]),
        height=int(record["height"]),
        rights_status=str(record["rights_status"]),
        rights_evidence=(
            str(record["rights_evidence"])
            if record.get("rights_evidence") is not None
            else None
        ),
    )


def _validate_and_enrich(
    record: dict[str, object],
    raw: Path,
    url: str,
    video_id: str,
    rights_status: str,
    rights_evidence: str | None,
) -> dict[str, object]:
    measured = validation.probe(raw)
    return {
        **record,
        "video_id": video_id,
        "source_url": url,
        "duration_s": measured.duration_s,
        "width": measured.width,
        "height": measured.height,
        "source_hash": validation.sha256(raw),
        "rights_status": rights_status,
        "rights_evidence": rights_evidence,
        "validation_status": "valid",
    }


def _download_video(url: str, directory: Path, raw: Path) -> dict:
    """Fetch the muxed video to raw.mp4, via a partial name.

    Downloading straight to raw.mp4 would leave a plausible-looking file behind
    if the process died mid-transfer, and the cache check above would then
    accept it.
    """
    _clear_partials(directory)

    ydl_opts = {
        "format": "bestvideo*+bestaudio/best",
        "merge_output_format": "mp4",
        "outtmpl": f"{directory / _PARTIAL_PREFIX}.%(ext)s",
        "quiet": True,
        "noprogress": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as exc:
        _clear_partials(directory)
        raise DownloadError(f"failed to download {url!r}: {exc}") from exc

    produced = next(iter(directory.glob(f"{_PARTIAL_PREFIX}.*")), None)
    if produced is None:
        raise DownloadError(f"yt-dlp reported success but wrote nothing for {url!r}")

    # A single-format result can arrive as .webm despite merge_output_format,
    # which only applies when merging. The name is normalised to raw.mp4
    # regardless: everything downstream reads it through ffmpeg, which sniffs
    # the container rather than trusting the extension.
    produced.rename(raw)
    return info or {}


def _extract_audio(raw: Path, audio: Path) -> None:
    """Derive 16 kHz mono PCM — the format Whisper resamples to internally.

    Doing it once here means every transcription and re-transcription skips
    that conversion, and the wav is small enough to keep after the video is
    evicted by the retention sweep.
    """
    partial = audio.with_name(f"{audio.stem}.part.wav")
    command = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(raw),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        str(partial),
    ]

    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        partial.unlink(missing_ok=True)
        raise AudioExtractionError(
            f"ffmpeg could not extract audio from {raw}: {exc.stderr.strip()}"
        ) from exc

    partial.rename(audio)


def clear_partials(video_id: str) -> None:
    """Drop a video's half-downloaded files.

    Called when a job is known dead. `_clear_partials` below covers the paths
    yt-dlp reports a failure on and the start of the next attempt; a process
    that was killed reaches neither.
    """
    directory = video_dir(video_id)
    if directory.is_dir():
        _clear_partials(directory)


def _clear_partials(directory: Path) -> None:
    for leftover in directory.glob(f"{_PARTIAL_PREFIX}.*"):
        leftover.unlink(missing_ok=True)
