"""Versioned delivery contract for rendered media.

The renderer may create many intermediate files, but publishing is allowed to
trust only a ``ready`` manifest.  This module owns stable paths, deterministic
asset identities, media probing, topology validation, and atomic writes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

import library


SCHEMA_VERSION = "media-manifest.v1"
MANIFEST_NAME = "media-manifest.json"
WHOLE_ITEM = "whole"

AssetRole = Literal[
    "clean_whole",
    "clean_vertical",
    "branded_whole",
    "branded_vertical",
]
ManifestState = Literal["building", "validating", "ready", "needs_action", "stale"]

_BRAND_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_PART_PATTERN = re.compile(r"^part_([1-9][0-9]*)$")


class ManifestValidationError(Exception):
    """An asset cannot satisfy the delivery contract."""


class ManifestConflictError(Exception):
    """A caller tried to replace an immutable render revision."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProbeEvidence(StrictModel):
    duration_s: float = Field(gt=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    video_codec: str = Field(min_length=1)
    audio_codec: str = Field(min_length=1)
    has_video: Literal[True] = True
    has_audio: Literal[True] = True


class MediaAsset(StrictModel):
    asset_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    video_id: str = Field(pattern=r"^[A-Za-z0-9_-]{11}$")
    render_revision: int = Field(ge=1)
    role: AssetRole
    content_item_id: str
    brand_id: str | None = None
    lineage_asset_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bytes: int = Field(gt=0)
    probe: ProbeEvidence
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_role(self) -> "MediaAsset":
        vertical = self.role.endswith("vertical")
        branded = self.role.startswith("branded")

        if vertical:
            if _PART_PATTERN.fullmatch(self.content_item_id) is None:
                raise ValueError("vertical assets require content_item_id part_<n>")
            if (self.probe.width, self.probe.height) != (1080, 1920):
                raise ValueError("vertical assets must be 1080x1920")
        else:
            if self.content_item_id != WHOLE_ITEM:
                raise ValueError("whole assets require content_item_id 'whole'")
            if (self.probe.width, self.probe.height) != (1920, 1080):
                raise ValueError("whole assets must be 1920x1080")

        if self.probe.video_codec != "h264" or self.probe.audio_codec != "aac":
            raise ValueError("delivery assets require H.264 video and AAC audio")

        if branded:
            _validate_brand_id(self.brand_id)
            if self.lineage_asset_id is None:
                raise ValueError("branded assets require clean-master lineage")
        elif self.brand_id is not None or self.lineage_asset_id is not None:
            raise ValueError("clean assets cannot carry brand or lineage fields")
        return self


class ManifestFailure(StrictModel):
    asset_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    retryable: bool


class MediaManifest(StrictModel):
    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    video_id: str = Field(pattern=r"^[A-Za-z0-9_-]{11}$")
    render_revision: int = Field(ge=1)
    metadata_revision_id: str | None = None
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    state: ManifestState
    chunk_names: list[str]
    brand_ids: list[str]
    assets: list[MediaAsset]
    failures: list[ManifestFailure] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @model_validator(mode="after")
    def validate_contract(self) -> "MediaManifest":
        expected_chunks = [f"part_{number}" for number in range(1, len(self.chunk_names) + 1)]
        if self.chunk_names != expected_chunks:
            raise ValueError("chunk_names must be contiguous and ordered from part_1")
        if len(self.brand_ids) != len(set(self.brand_ids)):
            raise ValueError("brand_ids must be unique")
        for brand_id in self.brand_ids:
            _validate_brand_id(brand_id)

        asset_ids = [asset.asset_id for asset in self.assets]
        paths = [asset.path for asset in self.assets]
        identities = [
            (asset.role, asset.content_item_id, asset.brand_id)
            for asset in self.assets
        ]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("asset_id values must be unique")
        if len(paths) != len(set(paths)):
            raise ValueError("asset paths must be unique")
        if len(identities) != len(set(identities)):
            raise ValueError("asset role/content/brand identities must be unique")

        for asset in self.assets:
            if asset.video_id != self.video_id:
                raise ValueError("all assets must belong to the manifest video")
            if asset.render_revision != self.render_revision:
                raise ValueError("all assets must belong to the manifest render revision")
            expected = expected_asset_path(
                self.video_id,
                self.render_revision,
                asset.role,
                asset.content_item_id,
                asset.brand_id,
            )
            if Path(asset.path).resolve(strict=False) != expected.resolve(strict=False):
                raise ValueError(f"asset path does not match its identity: {asset.asset_id}")

        if self.state == "ready":
            if self.failures:
                raise ValueError("a ready manifest cannot contain failures")
            self._validate_ready_topology()
        return self

    def _validate_ready_topology(self) -> None:
        expected = {("clean_whole", WHOLE_ITEM, None)}
        expected.update(("clean_vertical", part, None) for part in self.chunk_names)
        for brand_id in self.brand_ids:
            expected.add(("branded_whole", WHOLE_ITEM, brand_id))
            expected.update(
                ("branded_vertical", part, brand_id) for part in self.chunk_names
            )

        actual = {
            (asset.role, asset.content_item_id, asset.brand_id)
            for asset in self.assets
        }
        if actual != expected:
            missing = sorted(expected - actual, key=str)
            unexpected = sorted(actual - expected, key=str)
            raise ValueError(
                f"ready topology mismatch; missing={missing}, unexpected={unexpected}"
            )

        by_identity = {
            (asset.role, asset.content_item_id, asset.brand_id): asset
            for asset in self.assets
        }
        for brand_id in self.brand_ids:
            for content_item_id in [WHOLE_ITEM, *self.chunk_names]:
                branded_role = (
                    "branded_whole" if content_item_id == WHOLE_ITEM else "branded_vertical"
                )
                clean_role = (
                    "clean_whole" if content_item_id == WHOLE_ITEM else "clean_vertical"
                )
                branded = by_identity[(branded_role, content_item_id, brand_id)]
                clean = by_identity[(clean_role, content_item_id, None)]
                if branded.lineage_asset_id != clean.asset_id:
                    raise ValueError(
                        f"{branded.asset_id} must reference clean asset {clean.asset_id}"
                    )


def _validate_brand_id(brand_id: str | None) -> None:
    if brand_id is None or _BRAND_ID_PATTERN.fullmatch(brand_id) is None:
        raise ValueError("brand_id must use lowercase letters, numbers, '_' or '-'")


def _validate_part(content_item_id: str) -> None:
    if _PART_PATTERN.fullmatch(content_item_id) is None:
        raise ValueError("content_item_id must be part_<positive number>")


def manifest_revision_path(video_id: str, render_revision: int) -> Path:
    _validate_revision(render_revision)
    return _within_video(
        video_id,
        library.video_dir(video_id) / "manifests" / "revision" / f"{render_revision}.json",
    )


def current_manifest_path(video_id: str) -> Path:
    return _within_video(video_id, library.video_dir(video_id) / MANIFEST_NAME)


def expected_asset_path(
    video_id: str,
    render_revision: int,
    role: AssetRole,
    content_item_id: str,
    brand_id: str | None = None,
) -> Path:
    _validate_revision(render_revision)
    root = library.video_dir(video_id) / "outputs"
    file_name: str
    if role.endswith("whole"):
        if content_item_id != WHOLE_ITEM:
            raise ValueError("whole assets require content_item_id 'whole'")
        file_name = "whole-16x9.mp4"
    else:
        _validate_part(content_item_id)
        file_name = f"{content_item_id}-9x16.mp4"

    if role.startswith("clean"):
        if brand_id is not None:
            raise ValueError("clean assets cannot have a brand_id")
        path = root / "clean" / "revision" / str(render_revision)
    else:
        _validate_brand_id(brand_id)
        path = root / "brands" / str(brand_id) / "revision" / str(render_revision)

    if role.endswith("vertical"):
        path /= "vertical"
    return _within_video(video_id, path / file_name)


def deterministic_asset_id(
    video_id: str,
    render_revision: int,
    role: AssetRole,
    content_item_id: str,
    brand_id: str | None = None,
) -> str:
    # Validation and canonical spelling come from the path contract.
    expected_asset_path(video_id, render_revision, role, content_item_id, brand_id)
    identity = "|".join(
        [SCHEMA_VERSION, video_id, str(render_revision), role, content_item_id, brand_id or "-"]
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]


def inspect_expected_asset(
    video_id: str,
    render_revision: int,
    role: AssetRole,
    content_item_id: str,
    expected_duration_s: float,
    *,
    brand_id: str | None = None,
    lineage_asset_id: str | None = None,
    duration_tolerance_s: float = 0.5,
    warnings: list[str] | None = None,
) -> MediaAsset:
    path = expected_asset_path(
        video_id, render_revision, role, content_item_id, brand_id
    )
    if not path.is_file() or path.stat().st_size <= 0:
        raise ManifestValidationError(f"required asset is missing or empty: {path}")

    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type,codec_name,width,height:format=duration",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(completed.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError) as exc:
        raise ManifestValidationError(f"ffprobe could not read {path}: {exc}") from exc

    streams = payload.get("streams") or []
    video_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "video"), None
    )
    audio_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "audio"), None
    )
    if video_stream is None or audio_stream is None:
        raise ManifestValidationError(f"asset must contain video and audio streams: {path}")

    try:
        duration_s = float(payload["format"]["duration"])
        width = int(video_stream["width"])
        height = int(video_stream["height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ManifestValidationError(f"ffprobe returned incomplete evidence for {path}") from exc

    expected_size = (1080, 1920) if role.endswith("vertical") else (1920, 1080)
    if (width, height) != expected_size:
        raise ManifestValidationError(
            f"asset dimensions {width}x{height} do not match "
            f"{expected_size[0]}x{expected_size[1]}: {path}"
        )
    if abs(duration_s - expected_duration_s) > duration_tolerance_s:
        raise ManifestValidationError(
            f"asset duration {duration_s:.3f}s differs from expected "
            f"{expected_duration_s:.3f}s by more than {duration_tolerance_s:.3f}s: {path}"
        )
    if video_stream.get("codec_name") != "h264" or audio_stream.get("codec_name") != "aac":
        raise ManifestValidationError(f"asset must use H.264 video and AAC audio: {path}")

    return MediaAsset(
        asset_id=deterministic_asset_id(
            video_id, render_revision, role, content_item_id, brand_id
        ),
        video_id=video_id,
        render_revision=render_revision,
        role=role,
        content_item_id=content_item_id,
        brand_id=brand_id,
        lineage_asset_id=lineage_asset_id,
        path=str(path),
        sha256=_sha256(path),
        bytes=path.stat().st_size,
        probe=ProbeEvidence(
            duration_s=round(duration_s, 3),
            width=width,
            height=height,
            video_codec=str(video_stream["codec_name"]),
            audio_codec=str(audio_stream["codec_name"]),
        ),
        warnings=warnings or [],
    )


def write_manifest(manifest: MediaManifest) -> tuple[Path, Path]:
    revision_path = manifest_revision_path(manifest.video_id, manifest.render_revision)
    current_path = current_manifest_path(manifest.video_id)
    payload = manifest.model_dump_json(indent=2)

    # Intermediate state belongs only in the replaceable current pointer. A
    # needs_action revision must be able to become ready after retry; writing
    # it into immutable history here would permanently block that transition.
    if manifest.state == "ready":
        if revision_path.exists():
            existing = MediaManifest.model_validate_json(
                revision_path.read_text(encoding="utf-8")
            )
            if existing != manifest:
                raise ManifestConflictError(
                    f"render revision {manifest.render_revision} is immutable"
                )
        else:
            _atomic_write(revision_path, payload)
    _atomic_write(current_path, payload)
    return revision_path, current_path


def json_schema() -> dict:
    return MediaManifest.model_json_schema()


def _validate_revision(render_revision: int) -> None:
    if (
        not isinstance(render_revision, int)
        or isinstance(render_revision, bool)
        or render_revision < 1
    ):
        raise ValueError("render_revision must be a positive integer")


def _within_video(video_id: str, candidate: Path) -> Path:
    root = library.video_dir(video_id).resolve(strict=False)
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise ValueError("asset path escapes the video directory")
    return candidate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.part")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
