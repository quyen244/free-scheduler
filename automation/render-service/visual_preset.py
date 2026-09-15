"""Versioned editor presets shared by the local UI and render-service.

The browser may construct a draft, but it is never trusted as a render input.
Only a JSON document written under ``published/<id>/v<revision>.json`` and
validated here is eligible for a workflow job.  This keeps a half-dragged UI
change from becoming a silent production layout change.

A preset is a *layout*, not a brand.  It records where things sit; the brand
profile records which logo and watermark files are drawn in those places.  One
published revision can therefore dress any number of brands without copying an
asset between them.
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


SCHEMA_VERSION = 2
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
_ASSET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_. -]{0,127}$")
_LAYER_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
# A matting job id, which is what identifies one alpha revision on disk.
_MATTING_REVISION = re.compile(r"^[0-9a-f]{32}$")

# The seven confirmed editor colours.  A layer stores the *name*, never the
# hex: the browser swatch and the ffmpeg draw then read the same table and
# cannot drift apart.  The values are display-calibrated rather than pure RGB
# because saturated primaries chroma-bleed through h264 4:2:0 at the size a
# title is drawn.
PALETTE: dict[str, str] = {
    "white": "#FFFFFF",
    "black": "#000000",
    "red": "#FF3B30",
    "yellow": "#FFD60A",
    "green": "#34C759",
    "blue": "#0A84FF",
    "purple": "#AF52DE",
}

ColorName = Literal["white", "black", "red", "yellow", "green", "blue", "purple"]

# What a text layer draws.  ``static`` is the literal string held in the
# preset; the others are resolved per render from the video metadata, so one
# published layout can title every episode of a series.
TextSource = Literal["static", "title", "part"]

# Default stacking.  Background is implicit and always the floor; everything
# else is reorderable in the editor.
Z_MAIN_VIDEO = 10
Z_HOST = 20
Z_LOGO = 30
Z_WATERMARK = 40
Z_TEXT = 50
Z_SUBTITLE = 60


def hex_for(color: str) -> str:
    """Resolve a palette name to hex, rejecting anything outside the seven."""
    try:
        return PALETTE[color]
    except KeyError:
        raise RenderError(f"{color!r} is not one of the seven editor colours") from None


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


class Layer(Rect):
    """A placed rectangle that also takes part in stacking order."""

    z: int = Field(default=0, ge=0, le=99)
    visible: bool = True


class TextLayer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source: TextSource = "static"
    # Held for every source: for a bound field it is the preview string the
    # editor shows, so an operator can size the box against realistic text
    # before any render exists.
    text: str = Field(default="", max_length=300)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    size: int = Field(default=48, ge=12, le=220)
    color: ColorName = "white"
    align: Literal["left", "center", "right"] = "center"
    # Wrapping happens before the text reaches ffmpeg; drawtext has no word
    # wrap of its own and would run a title straight off the canvas.
    max_chars_per_line: int = Field(default=28, ge=6, le=120)
    max_lines: int = Field(default=3, ge=1, le=6)
    line_spacing: float = Field(default=1.15, ge=0.8, le=2.5)
    z: int = Field(default=Z_TEXT, ge=0, le=99)
    visible: bool = True

    @model_validator(mode="after")
    def check(self) -> "TextLayer":
        if not _LAYER_ID.fullmatch(self.id):
            raise ValueError(
                "a text layer id must be lowercase letters, numbers, and hyphens"
            )
        if self.x + self.w > 1.000001:
            raise ValueError("a text layer must remain inside its canvas")
        return self


class SubtitleStyle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Measured from the top like every other coordinate here; subs.build turns
    # it into the ASS bottom margin.
    y: float = Field(default=0.755, ge=0, le=1)
    size: int = Field(default=46, ge=12, le=160)
    color: ColorName = "white"
    outline_color: ColorName = "black"
    outline: int = Field(default=3, ge=0, le=12)
    align: Literal["left", "center", "right"] = "center"
    margin_ratio: float = Field(default=0.06, ge=0, le=0.4)
    max_chars_per_line: int = Field(default=38, ge=10, le=120)
    max_lines: int = Field(default=2, ge=1, le=5)
    z: int = Field(default=Z_SUBTITLE, ge=0, le=99)
    visible: bool = True


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
    # Where the real source video lands.  The editor aligns it against a still
    # mock image; nothing about that mock reaches this rectangle.
    main_video: Layer = Field(
        default_factory=lambda: Layer(x=0.0, y=0.28, w=1.0, h=0.44, z=Z_MAIN_VIDEO)
    )
    host: Layer = Field(
        default_factory=lambda: Layer(x=0.06, y=0.43, w=0.38, h=0.46, z=Z_HOST)
    )
    # Placement only.  The image drawn here comes from the selected brand
    # profile, so one layout serves every brand.
    logo: Layer = Field(
        default_factory=lambda: Layer(x=0.04, y=0.04, w=0.16, h=0.10, z=Z_LOGO)
    )
    watermark: Layer = Field(
        default_factory=lambda: Layer(x=0.79, y=0.04, w=0.16, h=0.10, z=Z_WATERMARK)
    )
    # Children of the main video, normalized against the SOURCE frame rather
    # than the canvas.  A blur hides something inside the footage, so it has to
    # track that footage when the source resolution changes.
    blur_regions: list[Rect] = Field(default_factory=list, max_length=8)
    text_layers: list[TextLayer] = Field(default_factory=list, max_length=12)
    subtitle: SubtitleStyle = Field(default_factory=SubtitleStyle)
    mask_corrections: list[MaskCorrection] = Field(default_factory=list, max_length=50)
    intro_enabled: bool = True
    intro_duration_s: float = Field(default=8, ge=0, le=60)

    @model_validator(mode="after")
    def check(self) -> "AspectLayout":
        if self.background_asset is not None and not _ASSET.fullmatch(
            self.background_asset
        ):
            raise ValueError("asset names must be plain allowlisted filenames")
        ids = [layer.id for layer in self.text_layers]
        if len(ids) != len(set(ids)):
            raise ValueError("text layer ids must be unique within an aspect")
        return self


class VisualPreset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    preset_id: str
    name: str = Field(min_length=1, max_length=80)
    revision: int | None = Field(default=None, ge=1)
    status: Literal["draft", "published"] = "draft"
    # One host video dresses both aspects; only its framing differs, so the
    # file and its alpha revision live once at the top.
    host_asset: str | None = None
    host_alpha_revision: str | None = None
    # Preview scaffolding: a still standing in for the source video so a layout
    # can be aligned without waiting for a render.  Never reaches a filtergraph.
    mock_main_asset: str | None = None
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
        for name in (self.host_asset, self.mock_main_asset):
            if name is not None and not _ASSET.fullmatch(name):
                raise ValueError("asset names must be plain allowlisted filenames")
        if self.host_alpha_revision is not None and not _MATTING_REVISION.fullmatch(
            self.host_alpha_revision
        ):
            raise ValueError("host_alpha_revision must be a matting job id")
        return self


# ---------------------------------------------------------------------------
# v1 to v2 migration
# ---------------------------------------------------------------------------


def _nearest_palette(hex_colour: str) -> str:
    """Map a free hex colour onto the seven confirmed names.

    v1 stored arbitrary hex.  Refusing to load those drafts would throw away
    real work, and silently defaulting them to white would misreport what the
    operator chose, so the closest of the seven is taken and the editor then
    shows which swatch that was.
    """
    value = hex_colour.lstrip("#")
    try:
        red, green, blue = (int(value[at : at + 2], 16) for at in (0, 2, 4))
    except (ValueError, IndexError):
        return "white"
    best, best_distance = "white", None
    for name, candidate in PALETTE.items():
        other = candidate.lstrip("#")
        distance = sum(
            (component - int(other[at : at + 2], 16)) ** 2
            for component, at in zip((red, green, blue), (0, 2, 4))
        )
        if best_distance is None or distance < best_distance:
            best, best_distance = name, distance
    return best


def _upgrade_layout(old: dict, canvas: str) -> dict:
    vertical = canvas == "vertical_9_16"
    caption = old.get("caption") or {}
    text_layers = []
    if str(caption.get("text") or "").strip():
        text_layers.append(
            {
                "id": "caption",
                "source": "static",
                "text": str(caption["text"]),
                "x": 0.08,
                "y": float(caption.get("y", 0.82)),
                "w": 0.84,
                "size": int(caption.get("size", 42)),
                "color": _nearest_palette(str(caption.get("color", "#FFFFFF"))),
                "align": "center",
                "z": Z_TEXT,
            }
        )
    placement = {
        "host": (old.get("host") or {"x": 0.06, "y": 0.43, "w": 0.38, "h": 0.46}, Z_HOST),
        "logo": (old.get("logo") or {"x": 0.04, "y": 0.04, "w": 0.16, "h": 0.10}, Z_LOGO),
        "watermark": (
            old.get("watermark") or {"x": 0.79, "y": 0.04, "w": 0.16, "h": 0.10},
            Z_WATERMARK,
        ),
    }
    return {
        "canvas": canvas,
        "background_asset": old.get("background_asset"),
        # The rectangle v1 hard-coded inside clean_render_preset, promoted to
        # the editable slot it should always have been.
        "main_video": (
            {"x": 0.0, "y": 0.28, "w": 1.0, "h": 0.44, "z": Z_MAIN_VIDEO}
            if vertical
            else {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0, "z": Z_MAIN_VIDEO}
        ),
        **{key: {**rect, "z": z} for key, (rect, z) in placement.items()},
        "blur_regions": old.get("blur_regions") or [],
        "text_layers": text_layers,
        "subtitle": {
            "y": 0.755 if vertical else 0.88,
            "size": 46 if vertical else 42,
            "max_chars_per_line": 38 if vertical else 58,
        },
        "mask_corrections": old.get("mask_corrections") or [],
        "intro_enabled": bool(old.get("intro_enabled", True)),
        "intro_duration_s": float(old.get("intro_duration_s", 8)),
    }


def upgrade(payload: dict) -> dict:
    """Bring any supported stored document up to the current schema version."""
    version = int(payload.get("schema_version", 1))
    if version == SCHEMA_VERSION:
        return payload
    if version != 1:
        raise RenderError(f"unsupported editor preset schema_version {version}")
    vertical = payload.get("vertical") or {}
    landscape = payload.get("landscape") or {}
    upgraded = {
        "schema_version": SCHEMA_VERSION,
        "preset_id": payload.get("preset_id"),
        "name": payload.get("name"),
        "status": payload.get("status", "draft"),
        # v1 stored the host per aspect while the editor wrote the same file to
        # both, so the vertical one is the file rather than a choice.
        "host_asset": vertical.get("host_asset") or landscape.get("host_asset"),
        "mock_main_asset": None,
        "vertical": _upgrade_layout(vertical, "vertical_9_16"),
        "landscape": _upgrade_layout(landscape, "landscape_16_9"),
    }
    if payload.get("revision") is not None:
        upgraded["revision"] = payload["revision"]
    return upgraded


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------


def root() -> Path:
    return settings.data_dir / "preset-editor"


def assets_dir(preset_id: str) -> Path:
    _check_id(preset_id)
    return root() / "assets" / preset_id


def matting_dir(preset_id: str, revision: str) -> Path:
    _check_id(preset_id)
    if not _MATTING_REVISION.fullmatch(revision):
        raise PresetNotFoundError(f"not a valid matting revision: {revision!r}")
    return root() / "matting" / preset_id / revision


def alpha_mask_path(preset_id: str, revision: str) -> Path:
    return matting_dir(preset_id, revision) / "host-alpha-mask.mp4"


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
    directory = root() / "drafts"
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        try:
            preset = VisualPreset.model_validate(
                upgrade(json.loads(path.read_text(encoding="utf-8")))
            )
        except Exception:  # a bad local draft should not make all drafts disappear
            continue
        items.append(
            {"preset_id": preset.preset_id, "name": preset.name, "status": "draft"}
        )
    return items


def load_draft(preset_id: str) -> VisualPreset:
    path = draft_path(preset_id)
    if not path.is_file():
        raise PresetNotFoundError(f"no editor draft at {path}")
    return VisualPreset.model_validate(
        upgrade(json.loads(path.read_text(encoding="utf-8")))
    )


def save_draft(payload: dict[str, object]) -> VisualPreset:
    preset = VisualPreset.model_validate(upgrade(dict(payload)))
    if preset.status != "draft" or preset.revision is not None:
        raise RenderError("a draft must have status 'draft' and no revision")
    path = draft_path(preset.preset_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, preset.model_dump(mode="json", exclude_none=True))
    return preset


def publish(payload: dict[str, object]) -> VisualPreset:
    draft = VisualPreset.model_validate(upgrade(dict(payload)))
    if draft.status != "draft" or draft.revision is not None:
        raise RenderError("only an unversioned draft can be published")
    _validate_assets_exist(draft)
    revision_dir = root() / "published" / draft.preset_id
    existing = (
        [
            int(path.stem[1:])
            for path in revision_dir.glob("v*.json")
            if path.stem[1:].isdigit()
        ]
        if revision_dir.is_dir()
        else []
    )
    revision = max(existing, default=0) + 1
    body = draft.model_dump(mode="json", exclude_none=True)
    body.update({"status": "published", "revision": revision})
    body["content_sha256"] = _content_hash(body)
    published = VisualPreset.model_validate(body)
    _write_json(
        published_path(published.preset_id, revision),
        published.model_dump(mode="json", exclude_none=True),
    )
    return published


def load_published(preset_id: str, revision: int) -> VisualPreset:
    path = published_path(preset_id, revision)
    if not path.is_file():
        raise PresetNotFoundError(f"no published editor preset at {path}")
    preset = VisualPreset.model_validate(
        upgrade(json.loads(path.read_text(encoding="utf-8")))
    )
    if preset.status != "published" or preset.revision != revision:
        raise RenderError(f"published preset {preset_id}@{revision} has invalid identity")
    _validate_assets_exist(preset)
    return preset


# ---------------------------------------------------------------------------
# the render contract
# ---------------------------------------------------------------------------

_CANVAS = {"vertical": {"w": 1080, "h": 1920}, "landscape": {"w": 1920, "h": 1080}}


def _text_layer_config(layer: TextLayer) -> dict:
    return {
        "id": layer.id,
        "source": layer.source,
        "text": layer.text,
        "x": layer.x,
        "y": layer.y,
        "w": layer.w,
        "size": layer.size,
        "color": hex_for(layer.color),
        "align": layer.align,
        "max_chars_per_line": layer.max_chars_per_line,
        "max_lines": layer.max_lines,
        "line_spacing": layer.line_spacing,
        "z": layer.z,
    }


def clean_render_preset(
    preset: VisualPreset, aspect: Literal["vertical", "landscape"]
) -> dict:
    """Adapt an immutable editor revision to the renderer preset contract.

    Everything emitted here is brand-neutral, because a clean master is shared
    between brands.  The logo and watermark rectangles are still carried: the
    branded stage needs them to place that brand's files, and a rectangle
    describes geometry rather than any brand's content.
    """
    layout = preset.vertical if aspect == "vertical" else preset.landscape
    if not layout.background_asset:
        raise RenderError(f"{aspect} layout needs a background asset before render")

    host = None
    if preset.host_asset and layout.host.visible:
        host = {
            "asset": f"editor:{preset.preset_id}:{preset.host_asset}",
            "rect": layout.host.model_dump(mode="json"),
            # Without an alpha revision the host would composite as an opaque
            # rectangle over the layout, which is visibly wrong rather than
            # merely imperfect. The renderer refuses that instead of drawing it.
            "alpha_revision": preset.host_alpha_revision,
            "preset_id": preset.preset_id,
            "z": layout.host.z,
        }

    return {
        "canvas": _CANVAS[aspect],
        "background": f"editor:{preset.preset_id}:{layout.background_asset}",
        "video_rect": layout.main_video.model_dump(mode="json"),
        "video_z": layout.main_video.z,
        "video_visible": layout.main_video.visible,
        "blur_regions": [region.model_dump(mode="json") for region in layout.blur_regions],
        "host": host,
        "text_layers": [
            _text_layer_config(layer) for layer in layout.text_layers if layer.visible
        ],
        "subtitle": {
            "font": "DejaVu Sans",
            "y": layout.subtitle.y,
            "size": layout.subtitle.size,
            "color": hex_for(layout.subtitle.color),
            "outline_color": hex_for(layout.subtitle.outline_color),
            "outline": layout.subtitle.outline,
            "align": layout.subtitle.align,
            "margin_ratio": layout.subtitle.margin_ratio,
            "max_chars_per_line": layout.subtitle.max_chars_per_line,
            "max_lines": layout.subtitle.max_lines,
            "z": layout.subtitle.z,
            "visible": layout.subtitle.visible,
        },
        # Geometry for the branded stage. The files themselves arrive with the
        # brand profile, never from here.
        "brand_slots": {
            "logo": layout.logo.model_dump(mode="json"),
            "watermark": layout.watermark.model_dump(mode="json"),
        },
        "intro": {"enabled": layout.intro_enabled, "duration_s": layout.intro_duration_s},
        "encode": {
            "vcodec": "h264_nvenc",
            "fallback_vcodec": "libx264",
            "crf": 21,
            "acodec": "aac",
            "abr": "128k" if aspect == "vertical" else "192k",
        },
        "editor_preset": {"preset_id": preset.preset_id, "revision": preset.revision},
    }


def _validate_assets_exist(preset: VisualPreset) -> None:
    directory = assets_dir(preset.preset_id)
    required = {preset.host_asset, preset.mock_main_asset}
    for layout in (preset.vertical, preset.landscape):
        required.add(layout.background_asset)
    missing = [
        name for name in sorted(filter(None, required)) if not (directory / name).is_file()
    ]
    if missing:
        raise RenderError(f"published preset references missing assets: {', '.join(missing)}")
    if preset.host_alpha_revision:
        alpha = alpha_mask_path(preset.preset_id, preset.host_alpha_revision)
        if not alpha.is_file():
            raise RenderError(
                f"published preset references a missing host alpha mask: {alpha}"
            )


def _content_hash(body: dict[str, object]) -> str:
    hashable = dict(body)
    hashable.pop("content_sha256", None)
    return hashlib.sha256(
        json.dumps(
            hashable, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _check_id(value: str) -> None:
    if not _ID.fullmatch(value):
        raise PresetNotFoundError(f"not a valid editor preset id: {value!r}")


def _write_json(path: Path, body: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
