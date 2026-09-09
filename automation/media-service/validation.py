"""Source-media validation performed before transcription."""

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from errors import SourcePolicyError


MIN_DURATION_S = 300.0
MAX_DURATION_S = 1200.0
MIN_HEIGHT = 720
ALLOWED_RIGHTS = {"owned", "licensed", "permission", "public_domain"}


@dataclass(frozen=True)
class MediaProbe:
    duration_s: float
    width: int
    height: int


def validate_rights(rights_status: str) -> None:
    if rights_status not in ALLOWED_RIGHTS:
        raise SourcePolicyError(
            "rights_unknown",
            "Declare rights as owned, licensed, permission, or public_domain.",
        )


def validate_probe(probe: MediaProbe) -> None:
    if not MIN_DURATION_S <= probe.duration_s <= MAX_DURATION_S:
        raise SourcePolicyError(
            "duration_out_of_range",
            "Video duration must be between 5:00 and 20:00 inclusive.",
        )
    if probe.height < MIN_HEIGHT:
        raise SourcePolicyError(
            "resolution_too_low",
            "Video resolution must be at least 720p.",
        )


def probe(path: Path) -> MediaProbe:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height:format=duration",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(result.stdout)
        stream = payload["streams"][0]
        measured = MediaProbe(
            duration_s=float(payload["format"]["duration"]),
            width=int(stream["width"]),
            height=int(stream["height"]),
        )
    except (subprocess.CalledProcessError, FileNotFoundError, KeyError, IndexError, ValueError, json.JSONDecodeError) as exc:
        raise SourcePolicyError(
            "media_corrupt",
            "Downloaded media cannot be opened and measured by ffprobe.",
        ) from exc
    validate_probe(measured)
    return measured


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
