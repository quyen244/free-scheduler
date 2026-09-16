"""Uploading a file into a brand, and matting a presenter video once it lands.

Kept apart from ``brands`` on purpose: that module is schema and storage and
runs without ffmpeg, which is what makes it testable as arithmetic.  Everything
here shells out.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import brands
import matting

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".webm", ".mkv"}
AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".aac"}


def safe_name(filename: str) -> str:
    """Reduce an uploaded name to the allowlist the schema accepts.

    Uploads arrive with whatever the operating system allowed: spaces, Vietnamese
    diacritics, parentheses.  Rewriting once here means the stored config, the
    ffmpeg command line, and the URL the editor fetches all agree.
    """
    stem = Path(filename).stem
    suffix = Path(filename).suffix.lower()
    cleaned = _SAFE.sub("-", stem).strip("-.") or "asset"
    return f"{cleaned[:100]}{suffix}"


def kind_for(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in VIDEO_SUFFIXES:
        return "video"
    if suffix in AUDIO_SUFFIXES:
        return "audio"
    raise ValueError(f"unsupported file type: {suffix or filename!r}")


def probe(path: Path) -> dict[str, object]:
    """Width, height and duration, tolerant of stills.

    ``render.probe`` insists on a container duration, which a PNG does not
    have.  An editor upload has to survive that, so a missing field here is a
    missing value rather than an exception.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height:format=duration",
                "-of", "json", str(path),
            ],
            check=True, capture_output=True, text=True,
        )
        data = json.loads(result.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return {}
    stream = (data.get("streams") or [{}])[0]
    duration = (data.get("format") or {}).get("duration")
    probed: dict[str, object] = {}
    if stream.get("width") and stream.get("height"):
        probed["w"] = int(stream["width"])
        probed["h"] = int(stream["height"])
    try:
        value = float(duration)
    except (TypeError, ValueError):
        value = None
    # A still reports a nominal duration in some containers; only a real
    # timeline is worth recording, and 0 would read as "empty clip".
    if value and value > 0:
        probed["duration_s"] = round(value, 3)
    return probed


def store(brand_id: str, asset_id: str, filename: str, body: bytes, role: str) -> dict:
    """Write an uploaded file into the brand folder and describe it.

    The caller merges the returned record into the draft.  Storage and the
    draft are deliberately two steps: a file on disk that no layer references
    is harmless, while a draft referencing a file that failed to write is not.
    """
    if not body:
        raise ValueError("the uploaded file is empty")
    name = safe_name(filename)
    kind = kind_for(name)
    directory = brands.assets_dir(brand_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_bytes(body)
    temporary.replace(path)

    if kind == "audio":
        # Music is not an asset a layer can place, so it is reported back on
        # its own and the caller puts it in `signature_music`.
        return {"kind": "audio", "file": name, "role": "music", **probe(path)}
    return {
        "id": asset_id,
        "kind": kind,
        "file": name,
        "role": role,
        **probe(path),
    }


def mat_host(brand_id: str, filename: str, corrections: list[dict]) -> dict[str, object]:
    """Run RVM over a presenter video already stored in the brand folder.

    Synchronous on purpose at this size: the alpha lands beside the video as
    ``host-alpha.mp4`` so the pair can never drift apart, and the editor has
    nothing useful to show until it exists.
    """
    source = brands.asset_file(brand_id, filename)
    if not source.is_file():
        raise FileNotFoundError(f"no such brand asset: {filename}")
    revision = uuid.uuid4().hex
    workdir = brands.brand_dir(brand_id) / "matting" / revision
    result = matting.mat_file(source, workdir, corrections=corrections)
    shutil.copy2(
        Path(str(result["alpha_mask_path"])), brands.asset_file(brand_id, "host-alpha.mp4")
    )
    preview = Path(str(result["alpha_preview_path"]))
    if preview.is_file():
        shutil.copy2(preview, brands.asset_file(brand_id, "host-alpha-preview.png"))
    return {
        "alpha_file": "host-alpha.mp4",
        "alpha_revision": revision,
        "preview_file": "host-alpha-preview.png" if preview.is_file() else None,
        "matting": result,
    }
