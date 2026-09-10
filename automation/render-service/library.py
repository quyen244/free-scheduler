"""Everything this service reads from and writes to the shared volume."""

import json
import re
from pathlib import Path

from config import settings
from errors import (
    InvalidVideoIdError,
    PresetNotFoundError,
    TranscriptNotFoundError,
    VoiceTrackNotFoundError,
)

TRANSCRIPT_NAME = "transcript.json"
TRANSLATED_NAME = "transcript.vi.json"
VOICE_NAME = "voice.wav"
VOICE_MANIFEST_NAME = "voice.json"
RAW_NAME = "raw.mp4"

PRESETS_DIR = "presets"
BACKGROUNDS_DIR = "backgrounds"
BRANDS_DIR = "brands"
MUSIC_DIR = "music"

_VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
# Presets are named in a request and joined to a path, so the same rule
# applies to them as to a video id.
_PRESET_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_ASSET_FILE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def video_dir(video_id: str) -> Path:
    if not _VIDEO_ID_PATTERN.fullmatch(video_id):
        raise InvalidVideoIdError(f"not a valid video id: {video_id!r}")
    return settings.data_dir / video_id


def load_transcript(video_id: str) -> dict:
    """The transcript to speak — the Vietnamese one when it exists.

    Same rule as chunking: a video that went through translation is voiced
    from the translation, and a Vietnamese source has only the one file.
    """
    directory = video_dir(video_id)
    for name in (TRANSLATED_NAME, TRANSCRIPT_NAME):
        path = directory / name
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    raise TranscriptNotFoundError(
        f"no transcript for {video_id!r} — POST it to the transcript "
        f"service's /transcribe first"
    )


def raw_path(video_id: str) -> Path:
    return video_dir(video_id) / RAW_NAME


def voice_path(video_id: str) -> Path:
    return video_dir(video_id) / VOICE_NAME


def load_voice_track(video_id: str) -> Path:
    path = voice_path(video_id)
    if not path.is_file():
        raise VoiceTrackNotFoundError(
            f"no voice track for {video_id!r} — POST it to /voice/jobs first"
        )
    return path


def save_voice_manifest(video_id: str, payload: dict) -> Path:
    path = video_dir(video_id) / VOICE_MANIFEST_NAME
    partial = path.with_name(VOICE_MANIFEST_NAME + ".part")
    partial.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    partial.replace(path)
    return path


def load_voice_manifest(video_id: str) -> dict | None:
    path = video_dir(video_id) / VOICE_MANIFEST_NAME
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def chunk_dir(video_id: str, idx: int) -> Path:
    return video_dir(video_id) / "chunks" / f"{idx:03d}"


def processed_dir(video_id: str) -> Path:
    return video_dir(video_id) / "processed"


def presets_dir() -> Path:
    return settings.data_dir / PRESETS_DIR


def load_preset(name: str) -> dict:
    if not _PRESET_PATTERN.fullmatch(name):
        raise PresetNotFoundError(f"not a valid preset name: {name!r}")
    path = presets_dir() / f"{name}.json"
    if not path.is_file():
        raise PresetNotFoundError(f"no preset at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def background_path(file_name: str) -> Path:
    return presets_dir() / BACKGROUNDS_DIR / Path(file_name).name


def asset_path(file_name: str) -> Path:
    return presets_dir() / Path(file_name).name


def brand_config_path(brand_id: str) -> Path:
    """A version-controlled mock brand config below ``data/presets/brands``.

    Durable brand profiles will later move into the application database.  For
    this pipeline-correctness slice, keeping configuration with the render
    presets makes the mock deterministic without allowing request data to pick
    an arbitrary local file.
    """
    if not _PRESET_PATTERN.fullmatch(brand_id):
        raise PresetNotFoundError(f"not a valid brand id: {brand_id!r}")
    path = presets_dir() / BRANDS_DIR / f"{brand_id}.json"
    if not path.is_file():
        raise PresetNotFoundError(f"no mock brand config at {path}")
    return path


def music_path(file_name: str) -> Path:
    """Resolve one allowlisted basename inside the shared ``data/music`` root."""
    if not _ASSET_FILE_PATTERN.fullmatch(file_name):
        raise PresetNotFoundError(f"not a valid music file name: {file_name!r}")
    path = settings.data_dir / MUSIC_DIR / file_name
    if not path.is_file():
        raise PresetNotFoundError(
            f"signature music is missing at {path}; add the configured file and retry"
        )
    return path
