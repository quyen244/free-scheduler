"""Versioned editor presets shared by the local UI and render-service.

The browser may construct a draft, but it is never trusted as a render input.
Only a JSON document written under ``published/<id>/v<revision>.json`` and
validated here is eligible for a workflow job.  This keeps a half-dragged UI
change from becoming a silent production layout change.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from config import settings
from errors import PresetNotFoundError, RenderError


SCHEMA_VERSION = 1
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
_ASSET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_. -]{0,127}$")


class Rect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def fits_canvas(self) -> "Rect":
        if self.x + self.w > 1.000001 or self.y + self.h > 1.000001:
            raise ValueError("a normalized rectangle must remain inside its canvas")
        return self


class TextStyle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(default="", max_length=300)
    y: float = Field(default=0.82, ge=0, le=1)
    size: int = Field(default=42, ge=12, le=160)
    color: str = Field(default="#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$")


class MaskCorrection(BaseModel):
    """A simple, deterministic all-frame correction for a mostly static host."""

    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    radius: float = Field(default=0.03, gt=0, le=0.2)
    mode: Literal["keep", "remove"]


class AspectLayout(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canvas: Literal["vertical_9_16", "landscape_16_9"]
    background_asset: str | None = None
    host_asset: str | None = None
    host: Rect = Field(default_factory=lambda: Rect(x=0.1, y=0.1, w=0.35, h=0.55))
    logo_asset: str | None = None
    logo: Rect = Field(default_factory=lambda: Rect(x=0.04, y=0.04, w=0.16, h=0.10))
    watermark_asset: str | None = None
    watermark: Rect = Field(default_factory=lambda: Rect(x=0.79, y=0.04, w=0.16, h=0.10))
    blur_regions: list[Rect] = Field(default_factory=list, max_length=8)
    caption: TextStyle = Field(default_factory=TextStyle)
    mask_corrections: list[MaskCorrection] = Field(default_factory=list, max_length=50)
    intro_enabled: bool = True
    intro_duration_s: float = Field(default=8, ge=0, le=60)

    @model_validator(mode="after")
    def asset_names_are_plain_files(self) -> "AspectLayout":
        for name in (
            self.background_asset,
            self.host_asset,
            self.logo_asset,
            self.watermark_asset,
        ):
            if name is not None and not _ASSET.fullmatch(name):
                raise ValueError("asset names must be plain allowlisted filenames")
        return self


class VisualPreset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    preset_id: str
    name: str = Field(min_length=1, max_length=80)
    revision: int | None = Field(default=None, ge=1)
    status: Literal["draft", "published"] = "draft"
    vertical: AspectLayout
    landscape: AspectLayout
    content_sha256: str | None = None

    @model_validator(mode="after")
    def validate_identity_and_aspects(self) -> "VisualPreset":
        if not _ID.fullmatch(self.preset_id):
            raise ValueError("preset_id must be lowercase letters, numbers, and hyphens")
        if self.vertical.canvas != "vertical_9_16":
            raise ValueError("vertical layout must use vertical_9_16")
        if self.landscape.canvas != "landscape_16_9":
            raise ValueError("landscape layout must use landscape_16_9")
        return self


def root() -> Path:
    return settings.data_dir / "preset-editor"


def assets_dir(preset_id: str) -> Path:
    _check_id(preset_id)
    return root() / "assets" / preset_id


def draft_path(preset_id: str) -> Path:
    _check_id(preset_id)
    return root() / "drafts" / f"{preset_id}.json"


def published_path(preset_id: str, revision: int) -> Path:
    _check_id(preset_id)
    if revision < 1:
        raise PresetNotFoundError("preset revision must be at least 1")
    return root() / "published" / preset_id / f"v{revision}.json"


def list_presets() -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for path in sorted((root() / "drafts").glob("*.json")) if (root() / "drafts").is_dir() else []:
        try:
            preset = VisualPreset.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:  # a bad local draft should not make all drafts disappear
            continue
        items.append({"preset_id": preset.preset_id, "name": preset.name, "status": "draft"})
    return items


def load_draft(preset_id: str) -> VisualPreset:
    path = draft_path(preset_id)
    if not path.is_file():
        raise PresetNotFoundError(f"no editor draft at {path}")
    return VisualPreset.model_validate_json(path.read_text(encoding="utf-8"))


def save_draft(payload: dict[str, object]) -> VisualPreset:
    preset = VisualPreset.model_validate(payload)
    if preset.status != "draft" or preset.revision is not None:
        raise RenderError("a draft must have status 'draft' and no revision")
    path = draft_path(preset.preset_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, preset.model_dump(mode="json", exclude_none=True))
    return preset


def publish(payload: dict[str, object]) -> VisualPreset:
    draft = VisualPreset.model_validate(payload)
    if draft.status != "draft" or draft.revision is not None:
        raise RenderError("only an unversioned draft can be published")
    _validate_assets_exist(draft)
    revision_dir = root() / "published" / draft.preset_id
    existing = [int(path.stem[1:]) for path in revision_dir.glob("v*.json") if path.stem[1:].isdigit()] if revision_dir.is_dir() else []
    revision = max(existing, default=0) + 1
    body = draft.model_dump(mode="json", exclude_none=True)
    body.update({"status": "published", "revision": revision})
    body["content_sha256"] = _content_hash(body)
    published = VisualPreset.model_validate(body)
    _write_json(published_path(published.preset_id, revision), published.model_dump(mode="json", exclude_none=True))
    return published


def load_published(preset_id: str, revision: int) -> VisualPreset:
    path = published_path(preset_id, revision)
    if not path.is_file():
        raise PresetNotFoundError(f"no published editor preset at {path}")
    preset = VisualPreset.model_validate_json(path.read_text(encoding="utf-8"))
    if preset.status != "published" or preset.revision != revision:
        raise RenderError(f"published preset {preset_id}@{revision} has invalid identity")
    _validate_assets_exist(preset)
    return preset


def clean_render_preset(preset: VisualPreset, aspect: Literal["vertical", "landscape"]) -> dict:
    """Adapt an immutable editor revision to the existing clean-master contract.

    This deliberately contains only brand-neutral clean-master properties. Host,
    logo, and image watermark remain in the editor revision for preview and the
    upcoming branded composition stage; putting them in a clean master would
    break the asset-sharing invariant between brands.
    """
    layout = preset.vertical if aspect == "vertical" else preset.landscape
    if not layout.background_asset:
        raise RenderError(f"{aspect} layout needs a background asset before render")
    canvas = {"vertical": {"w": 1080, "h": 1920}, "landscape": {"w": 1920, "h": 1080}}[aspect]
    if aspect == "vertical":
        video_rect = {"x": 0.0, "y": 0.28, "w": 1.0, "h": 0.44}
        subtitle = {"y": 0.755, "font": "DejaVu Sans", "size": 46, "color": "#FFFFFF", "outline_color": "#000000", "outline": 3, "max_chars_per_line": 38, "max_lines": 2}
        captions = {"caption_top": {"y": 0.115, "size": 62, "color": "#FFFFFF", "max_chars_per_line": 24}, "caption_bottom": {"y": 0.885, "size": 54, "color": "#D4AF37", "max_chars_per_line": 28}}
    else:
        video_rect = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
        subtitle = {"y": 0.88, "font": "DejaVu Sans", "size": 42, "color": "#FFFFFF", "outline_color": "#000000", "outline": 3, "max_chars_per_line": 58, "max_lines": 2}
        captions = {}
    return {
        "canvas": canvas,
        "background": f"editor:{preset.preset_id}:{layout.background_asset}",
        "video_rect": video_rect,
        "blur_regions": [region.model_dump(mode="json") for region in layout.blur_regions],
        "subtitle": subtitle,
        **captions,
        "encode": {"vcodec": "h264_nvenc", "fallback_vcodec": "libx264", "crf": 21, "acodec": "aac", "abr": "128k" if aspect == "vertical" else "192k"},
        "editor_preset": {"preset_id": preset.preset_id, "revision": preset.revision},
    }


def _validate_assets_exist(preset: VisualPreset) -> None:
    required = set()
    for layout in (preset.vertical, preset.landscape):
        for value in (layout.background_asset, layout.host_asset, layout.logo_asset, layout.watermark_asset):
            if value:
                required.add(value)
    directory = assets_dir(preset.preset_id)
    missing = [name for name in sorted(required) if not (directory / name).is_file()]
    if missing:
        raise RenderError(f"published preset references missing assets: {', '.join(missing)}")


def _content_hash(body: dict[str, object]) -> str:
    hashable = dict(body)
    hashable.pop("content_sha256", None)
    return hashlib.sha256(json.dumps(hashable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _check_id(value: str) -> None:
    if not _ID.fullmatch(value):
        raise PresetNotFoundError(f"not a valid editor preset id: {value!r}")


def _write_json(path: Path, body: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
