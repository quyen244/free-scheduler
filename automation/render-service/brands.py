"""A brand: its artwork and the layout that arranges it, in one folder.

This replaces the old split where a *visual preset* held the rectangles and a
*brand profile* held the logo file.  That split let one layout dress many
brands, which sounded economical and turned out to be the wrong unit: a channel
does not just swap its logo, it wants its own background, its own presenter,
its own title placement.  Describing that as "a shared layout plus overrides"
costs an operator two concepts and a merge rule to reason about every time.

So a brand owns everything:

    presets/brand/<brand_id>/
        config.json          the working draft
        v1.json, v2.json     immutable published revisions
        assets/              every uploaded file, plus matting output

and a render is a loop:

    for brand_id in brands:
        config = brands.load_published(brand_id, revision)
        composite(config.layout(aspect), config.assets)

Nothing is inferred at render time.  A layer names an asset id, that asset
names a file, and the file is either in the folder or the publish fails.

The layout is a flat, ordered list of layers rather than a fixed set of named
slots.  That is what lets the editor open on an empty black canvas and grow:
a slot that always exists has to be drawn as an empty box before anything is
uploaded into it, and five empty boxes is precisely the clutter this design is
replacing.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from config import settings
from errors import PresetNotFoundError, RenderError

SCHEMA_VERSION = "brand.v1"

_BRAND_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
_ASSET_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
_LAYER_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
# A matting job id, which is what identifies one alpha revision on disk.
_MATTING_REVISION = re.compile(r"^[0-9a-f]{32}$")

# The seven confirmed editor colours.  A layer stores the *name*, never the
# hex: the browser swatch and the ffmpeg draw then read the same table and
# cannot drift apart.
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

# What a text layer draws.  ``static`` is the literal string held in the brand;
# the others are resolved per render from the video metadata, so one published
# brand can title every episode of a series.
TextSource = Literal["static", "title", "part"]

CANVAS_SIZE = {
    "vertical_9_16": {"w": 1080, "h": 1920},
    "landscape_16_9": {"w": 1920, "h": 1080},
}


def hex_for(color: str) -> str:
    """Resolve a palette name to hex, rejecting anything outside the seven."""
    try:
        return PALETTE[color]
    except KeyError:
        raise RenderError(f"{color!r} is not one of the seven editor colours") from None


# ---------------------------------------------------------------------------
# assets
# ---------------------------------------------------------------------------


class Asset(BaseModel):
    """One uploaded file, addressable by a short id the layers refer to.

    Uploading and placing are separate acts: a logo uploaded once can appear in
    both the 9:16 and the 16:9 layout without a second copy, and re-uploading
    the file under the same id updates both placements at once.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["image", "video"]
    file: str
    # What the operator said this is, used by the editor to suggest a sensible
    # first rectangle.  Deliberately not load-bearing: the renderer reads the
    # layer's ``kind``, never this.
    role: Literal["background", "logo", "watermark", "host", "mock_main", "other"] = (
        "other"
    )
    # Probed on upload so the editor can show a true aspect ratio before any
    # render exists, and so a contain-fit preview matches what ffmpeg will do.
    w: int | None = Field(default=None, ge=1)
    h: int | None = Field(default=None, ge=1)
    duration_s: float | None = Field(default=None, ge=0)
    # Matting output for a presenter video.  Both travel together because an
    # alpha mask that belongs to a different take is worse than none at all.
    alpha_file: str | None = None
    alpha_revision: str | None = None

    @model_validator(mode="after")
    def check(self) -> "Asset":
        if not _ASSET_ID.fullmatch(self.id):
            raise ValueError("an asset id must be lowercase letters, numbers, hyphens")
        for name in (self.file, self.alpha_file):
            if name is not None and not _FILE.fullmatch(name):
                raise ValueError("asset filenames must be plain allowlisted names")
        if self.alpha_revision is not None and not _MATTING_REVISION.fullmatch(
            self.alpha_revision
        ):
            raise ValueError("alpha_revision must be a matting job id")
        if (self.alpha_file is None) != (self.alpha_revision is None):
            raise ValueError("an alpha mask and its revision must travel together")
        if self.alpha_file is not None and self.kind != "video":
            raise ValueError("only a video asset carries an alpha mask")
        return self


# ---------------------------------------------------------------------------
# layers
# ---------------------------------------------------------------------------


class _Placed(BaseModel):
    """Anything with a rectangle and a place in the stack."""

    model_config = ConfigDict(extra="forbid")

    id: str
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)
    z: int = Field(default=10, ge=0, le=99)
    visible: bool = True

    @model_validator(mode="after")
    def inside_canvas(self) -> "_Placed":
        if not _LAYER_ID.fullmatch(self.id):
            raise ValueError("a layer id must be lowercase letters, numbers, hyphens")
        if self.x + self.w > 1.000001 or self.y + self.h > 1.000001:
            raise ValueError(f"layer {self.id!r} runs outside its canvas")
        return self


class ImageLayer(_Placed):
    """A still placed on the canvas: background, logo, watermark, anything."""

    kind: Literal["image"] = "image"
    asset: str
    opacity: float = Field(default=1.0, gt=0, le=1)
    # Backgrounds want to fill their rectangle; a logo must keep its shape.
    fit: Literal["contain", "fill"] = "contain"


class MainVideoLayer(_Placed):
    """Where the source video lands.

    It carries no asset: the file arrives per render from the pipeline.  The
    editor aligns this rectangle against a still mock image, and nothing about
    that mock reaches the renderer.
    """

    kind: Literal["main_video"] = "main_video"
    fit: Literal["contain", "fill"] = "contain"


class BlurLayer(BaseModel):
    """A rectangle blurred out of the footage.

    Normalised against the SOURCE frame rather than the canvas, because a blur
    hides something *inside* the footage and has to track it when the source
    channel changes resolution.  Where it lands on the canvas follows from the
    main video's rectangle, which is why a blur needs one.

    ``z`` places it in the stack like any other layer: a blur above the logo
    blurs the logo, a blur below it does not.  ``None`` is the pre-stacking
    meaning - applied to the source's own pixels before the footage is placed,
    and so underneath everything by construction - kept so revisions published
    before blurs had a ``z`` still render the way they were approved.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["blur"] = "blur"
    id: str
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)
    z: int | None = Field(default=None, ge=0, le=99)
    visible: bool = True

    @model_validator(mode="after")
    def check(self) -> "BlurLayer":
        if not _LAYER_ID.fullmatch(self.id):
            raise ValueError("a layer id must be lowercase letters, numbers, hyphens")
        return self


class HostLayer(_Placed):
    """The presenter, composited through the alpha mask RVM produced."""

    kind: Literal["host"] = "host"
    asset: str


class TextLayer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["text"] = "text"
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
    z: int = Field(default=50, ge=0, le=99)
    visible: bool = True

    @model_validator(mode="after")
    def check(self) -> "TextLayer":
        if not _LAYER_ID.fullmatch(self.id):
            raise ValueError("a layer id must be lowercase letters, numbers, hyphens")
        if self.x + self.w > 1.000001:
            raise ValueError(f"text layer {self.id!r} runs outside its canvas")
        return self


class SubtitleLayer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["subtitle"] = "subtitle"
    id: str = "subtitle"
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
    z: int = Field(default=60, ge=0, le=99)
    visible: bool = True

    @model_validator(mode="after")
    def check(self) -> "SubtitleLayer":
        if not _LAYER_ID.fullmatch(self.id):
            raise ValueError("a layer id must be lowercase letters, numbers, hyphens")
        return self


AnyLayer = Annotated[
    Union[ImageLayer, MainVideoLayer, BlurLayer, HostLayer, TextLayer, SubtitleLayer],
    Field(discriminator="kind"),
]


# ---------------------------------------------------------------------------
# audio
# ---------------------------------------------------------------------------


class SpeechDucking(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: Literal[True] = True
    threshold: float = Field(gt=0, le=1)
    ratio: float = Field(ge=1, le=20)
    attack_ms: float = Field(ge=0.01, le=2000)
    release_ms: float = Field(ge=50, le=9000)


class SignatureMusic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    volume_db: float = Field(ge=-50, le=-12)
    loop: Literal[True] = True
    fade_in_s: float = Field(ge=0, le=5)
    fade_out_s: float = Field(ge=0, le=5)
    ducking: SpeechDucking

    @model_validator(mode="after")
    def fades_fit(self) -> "SignatureMusic":
        if self.fade_in_s + self.fade_out_s > 10:
            raise ValueError("combined music fades must not exceed ten seconds")
        return self


# ---------------------------------------------------------------------------
# the brand
# ---------------------------------------------------------------------------


class Layout(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canvas: Literal["vertical_9_16", "landscape_16_9"]
    # Starts empty on purpose.  A new brand is a black canvas; every layer on
    # it was put there by someone.
    layers: list[AnyLayer] = Field(default_factory=list, max_length=60)
    intro_enabled: bool = True
    intro_duration_s: float = Field(default=8, ge=0, le=60)

    @model_validator(mode="after")
    def check(self) -> "Layout":
        ids = [layer.id for layer in self.layers]
        if len(ids) != len(set(ids)):
            raise ValueError("layer ids must be unique within an aspect")
        for kind, limit in (("main_video", 1), ("subtitle", 1), ("host", 1)):
            found = [layer for layer in self.layers if layer.kind == kind]
            if len(found) > limit:
                raise ValueError(f"an aspect may hold at most {limit} {kind} layer")
        if self.blurs and not self.main_video:
            # A blur is a child of the source footage.  With no main video
            # there is nothing for it to hide, and silently dropping it would
            # hide the mistake instead.
            raise ValueError("a blur layer needs a main_video layer to apply to")
        return self

    @property
    def main_video(self) -> MainVideoLayer | None:
        return next(
            (layer for layer in self.layers if layer.kind == "main_video"), None
        )

    @property
    def subtitle(self) -> SubtitleLayer | None:
        return next((layer for layer in self.layers if layer.kind == "subtitle"), None)

    @property
    def host(self) -> HostLayer | None:
        return next((layer for layer in self.layers if layer.kind == "host"), None)

    @property
    def blurs(self) -> list[BlurLayer]:
        return [layer for layer in self.layers if layer.kind == "blur"]

    @property
    def images(self) -> list[ImageLayer]:
        return [layer for layer in self.layers if layer.kind == "image"]

    @property
    def texts(self) -> list[TextLayer]:
        return [layer for layer in self.layers if layer.kind == "text"]


class Brand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    brand_id: str
    display_name: str = Field(min_length=1, max_length=100)
    revision: int | None = Field(default=None, ge=1)
    status: Literal["draft", "published"] = "draft"
    assets: list[Asset] = Field(default_factory=list, max_length=40)
    # Preview scaffolding: the asset id of a still standing in for the source
    # video so a layout can be aligned without waiting for a render.  It never
    # reaches a filtergraph, which ``render_config`` enforces by not emitting
    # it at all.
    mock_main_asset: str | None = None
    signature_music: SignatureMusic | None = None
    vertical: Layout = Field(
        default_factory=lambda: Layout(canvas="vertical_9_16")
    )
    landscape: Layout = Field(
        default_factory=lambda: Layout(canvas="landscape_16_9")
    )
    content_sha256: str | None = None

    @model_validator(mode="after")
    def check(self) -> "Brand":
        if not _BRAND_ID.fullmatch(self.brand_id):
            raise ValueError("brand_id must be lowercase letters, numbers, and hyphens")
        if self.vertical.canvas != "vertical_9_16":
            raise ValueError("the vertical layout must use vertical_9_16")
        if self.landscape.canvas != "landscape_16_9":
            raise ValueError("the landscape layout must use landscape_16_9")
        ids = [asset.id for asset in self.assets]
        if len(ids) != len(set(ids)):
            raise ValueError("asset ids must be unique within a brand")
        known = set(ids)
        if self.mock_main_asset is not None and self.mock_main_asset not in known:
            raise ValueError("mock_main_asset names an asset that does not exist")
        for layout in (self.vertical, self.landscape):
            for layer in layout.layers:
                asset_id = getattr(layer, "asset", None)
                if asset_id is None:
                    continue
                if asset_id not in known:
                    raise ValueError(
                        f"layer {layer.id!r} names unknown asset {asset_id!r}"
                    )
                if asset_id == self.mock_main_asset:
                    # The mock stands in for footage the renderer supplies.
                    # Placing it as a real layer would publish a still into a
                    # delivery asset, which is the one thing it must never do.
                    raise ValueError(
                        f"layer {layer.id!r} places the preview-only mock asset"
                    )
                asset = self.asset(asset_id)
                wanted = "video" if layer.kind == "host" else "image"
                if asset.kind != wanted:
                    raise ValueError(
                        f"layer {layer.id!r} needs {wanted} asset, {asset_id!r} is {asset.kind}"
                    )
                if layer.kind == "host" and asset.alpha_file is None:
                    # An unmatted presenter composites as an opaque rectangle
                    # over the layout, which is visibly wrong rather than
                    # merely imperfect.
                    raise ValueError(
                        f"host asset {asset_id!r} has no alpha mask; run matting first"
                    )
        return self

    def asset(self, asset_id: str) -> Asset:
        for asset in self.assets:
            if asset.id == asset_id:
                return asset
        raise RenderError(f"brand {self.brand_id} has no asset {asset_id!r}")

    def layout(self, aspect: Literal["vertical", "landscape"]) -> Layout:
        return self.vertical if aspect == "vertical" else self.landscape


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------


def root() -> Path:
    return settings.data_dir / "presets" / "brand"


def brand_dir(brand_id: str) -> Path:
    _check_id(brand_id)
    return root() / brand_id


def assets_dir(brand_id: str) -> Path:
    return brand_dir(brand_id) / "assets"


def asset_file(brand_id: str, filename: str) -> Path:
    if not _FILE.fullmatch(filename):
        raise PresetNotFoundError(f"not a valid asset filename: {filename!r}")
    return assets_dir(brand_id) / filename


def draft_path(brand_id: str) -> Path:
    return brand_dir(brand_id) / "config.json"


def published_path(brand_id: str, revision: int) -> Path:
    if revision < 1:
        raise PresetNotFoundError("a brand revision must be at least 1")
    return brand_dir(brand_id) / f"v{revision}.json"


def list_brands() -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    directory = root()
    for path in sorted(directory.iterdir()) if directory.is_dir() else []:
        if not path.is_dir() or not _BRAND_ID.fullmatch(path.name):
            continue
        revisions = sorted(
            int(revision.stem[1:])
            for revision in path.glob("v*.json")
            if revision.stem[1:].isdigit()
        )
        display = path.name
        try:
            display = str(
                json.loads(draft_path(path.name).read_text(encoding="utf-8"))[
                    "display_name"
                ]
            )
        except Exception:  # a broken draft must not hide the brand itself
            pass
        items.append(
            {
                "brand_id": path.name,
                "display_name": display,
                "revisions": revisions,
                "latest_revision": revisions[-1] if revisions else None,
            }
        )
    return items


def load_draft(brand_id: str) -> Brand:
    path = draft_path(brand_id)
    if not path.is_file():
        raise PresetNotFoundError(f"no brand draft at {path}")
    return Brand.model_validate(json.loads(path.read_text(encoding="utf-8")))


def save_draft(payload: dict[str, object]) -> Brand:
    draft = Brand.model_validate(dict(payload))
    if draft.status != "draft" or draft.revision is not None:
        raise RenderError("a draft must have status 'draft' and no revision")
    _write_json(draft_path(draft.brand_id), draft.model_dump(mode="json", exclude_none=True))
    return draft


def create(brand_id: str, display_name: str) -> Brand:
    """Start a brand: a folder, an empty asset list, two black canvases."""
    if draft_path(brand_id).is_file():
        raise RenderError(f"brand {brand_id!r} already exists")
    assets_dir(brand_id).mkdir(parents=True, exist_ok=True)
    return save_draft(
        {"brand_id": brand_id, "display_name": display_name, "status": "draft"}
    )


def publish(payload: dict[str, object]) -> Brand:
    draft = Brand.model_validate(dict(payload))
    if draft.status != "draft" or draft.revision is not None:
        raise RenderError("only an unversioned draft can be published")
    _validate_files_exist(draft)
    existing = [
        int(path.stem[1:])
        for path in brand_dir(draft.brand_id).glob("v*.json")
        if path.stem[1:].isdigit()
    ]
    revision = max(existing, default=0) + 1
    body = draft.model_dump(mode="json", exclude_none=True)
    body.update({"status": "published", "revision": revision})
    body["content_sha256"] = _content_hash(body)
    published = Brand.model_validate(body)
    _write_json(
        published_path(published.brand_id, revision),
        published.model_dump(mode="json", exclude_none=True),
    )
    return published


def load_published(brand_id: str, revision: int) -> Brand:
    path = published_path(brand_id, revision)
    if not path.is_file():
        raise PresetNotFoundError(f"no published brand at {path}")
    brand = Brand.model_validate(json.loads(path.read_text(encoding="utf-8")))
    if brand.status != "published" or brand.revision != revision:
        raise RenderError(f"published brand {brand_id}@{revision} has invalid identity")
    _validate_files_exist(brand)
    return brand


def latest_revision(brand_id: str) -> int:
    revisions = [
        int(path.stem[1:])
        for path in brand_dir(brand_id).glob("v*.json")
        if path.stem[1:].isdigit()
    ]
    if not revisions:
        raise PresetNotFoundError(f"brand {brand_id!r} has no published revision")
    return max(revisions)


# ---------------------------------------------------------------------------
# the render contract
# ---------------------------------------------------------------------------


def _text_config(layer: TextLayer) -> dict:
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


def render_config(
    brand: Brand, aspect: Literal["vertical", "landscape"]
) -> dict:
    """Flatten one published brand into the renderer's preset contract.

    Every path is resolved here, once, against this brand's own asset folder.
    The renderer therefore never has to know what a brand is: it receives a
    canvas, a list of layers with files already attached, and draws them in
    ascending ``z``.
    """
    if brand.status != "published" or brand.revision is None:
        raise RenderError("only a published brand revision can be rendered")
    layout = brand.layout(aspect)
    canvas = CANVAS_SIZE[layout.canvas]

    main = layout.main_video
    if main is None or not main.visible:
        raise RenderError(
            f"{brand.brand_id}/{aspect} has no visible main_video layer, so the "
            "source footage would not appear in the render"
        )

    images = []
    for layer in layout.images:
        if not layer.visible:
            continue
        asset = brand.asset(layer.asset)
        images.append(
            {
                "id": layer.id,
                "path": str(asset_file(brand.brand_id, asset.file)),
                "x": layer.x,
                "y": layer.y,
                "w": layer.w,
                "h": layer.h,
                "opacity": layer.opacity,
                "fit": layer.fit,
                "z": layer.z,
            }
        )

    host = None
    host_layer = layout.host
    if host_layer is not None and host_layer.visible:
        asset = brand.asset(host_layer.asset)
        host = {
            "id": host_layer.id,
            "path": str(asset_file(brand.brand_id, asset.file)),
            "alpha_path": str(asset_file(brand.brand_id, str(asset.alpha_file))),
            "rect": {
                "x": host_layer.x,
                "y": host_layer.y,
                "w": host_layer.w,
                "h": host_layer.h,
            },
            "z": host_layer.z,
        }

    subtitle = layout.subtitle
    music = None
    if brand.signature_music is not None:
        music = {
            **brand.signature_music.model_dump(mode="json"),
            "path": str(asset_file(brand.brand_id, brand.signature_music.file)),
        }

    return {
        "brand": {
            "brand_id": brand.brand_id,
            "revision": brand.revision,
            "content_sha256": brand.content_sha256,
        },
        "canvas": canvas,
        "video_rect": {"x": main.x, "y": main.y, "w": main.w, "h": main.h},
        "video_fit": main.fit,
        "video_z": main.z,
        "video_visible": True,
        # Source-frame coordinates in both lists, which is why neither is in
        # `images`. The split is when they are applied: a blur with no `z` is
        # burned into the source before it is placed, a blur with one is
        # composited over whatever the stack had drawn by the time it is
        # reached.
        "blur_regions": [
            {"x": blur.x, "y": blur.y, "w": blur.w, "h": blur.h}
            for blur in layout.blurs
            if blur.visible and blur.z is None
        ],
        "blur_layers": [
            {"id": blur.id, "x": blur.x, "y": blur.y, "w": blur.w, "h": blur.h, "z": blur.z}
            for blur in layout.blurs
            if blur.visible and blur.z is not None
        ],
        "images": images,
        "host": host,
        "text_layers": [_text_config(layer) for layer in layout.texts if layer.visible],
        "subtitle": (
            {
                "font": "DejaVu Sans",
                "y": subtitle.y,
                "size": subtitle.size,
                "color": hex_for(subtitle.color),
                "outline_color": hex_for(subtitle.outline_color),
                "outline": subtitle.outline,
                "align": subtitle.align,
                "margin_ratio": subtitle.margin_ratio,
                "max_chars_per_line": subtitle.max_chars_per_line,
                "max_lines": subtitle.max_lines,
                "z": subtitle.z,
                "visible": True,
            }
            if subtitle is not None and subtitle.visible
            else None
        ),
        "signature_music": music,
        "intro": {
            "enabled": layout.intro_enabled,
            "duration_s": layout.intro_duration_s,
        },
        "encode": {
            "vcodec": "h264_nvenc",
            "fallback_vcodec": "libx264",
            "crf": 21,
            "acodec": "aac",
            "abr": "128k" if aspect == "vertical" else "192k",
        },
    }


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------


def _validate_files_exist(brand: Brand) -> None:
    """Fail the publish, not the render, when a file is missing.

    Publishing is the last cheap moment to notice.  After it, a missing file
    costs whatever the render had already spent before ffmpeg opened it.
    """
    missing: list[str] = []
    for asset in brand.assets:
        for name in (asset.file, asset.alpha_file):
            if name and not asset_file(brand.brand_id, name).is_file():
                missing.append(name)
    if brand.signature_music is not None:
        name = brand.signature_music.file
        if not asset_file(brand.brand_id, name).is_file():
            missing.append(name)
    if missing:
        raise RenderError(
            f"brand {brand.brand_id} references missing files: {', '.join(sorted(set(missing)))}"
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
    if not _BRAND_ID.fullmatch(value):
        raise PresetNotFoundError(f"not a valid brand id: {value!r}")


def _write_json(path: Path, body: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(
        json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)
