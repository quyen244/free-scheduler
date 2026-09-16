"""Turning a preset's 0-1 coordinates into pixels for one particular source.

Presets are normalised so one file survives a source channel changing
resolution. Pixel coordinates would keep working right up until that happens
and then silently mis-place a blur — which is exactly the failure nobody
notices until it is public. All the multiplication lives here, with no ffmpeg
and no files, so it can be checked against arithmetic.
"""

import subprocess
from dataclasses import dataclass, field
from functools import lru_cache

# h264 with yuv420p needs even dimensions in both axes. Rounding down at the
# very end is invisible; leaving it to ffmpeg is a "width not divisible by 2"
# failure three minutes into a render.
def _even(value: float) -> int:
    return max(int(round(value / 2.0)) * 2, 2)


# A position is not a size. Zero is a legal offset and is exactly what a
# full-bleed layout asks for, so clamping it to 2 like a dimension pushes the
# layer off by two pixels: on a 1920-wide video in a 1920 canvas that crops two
# pixels from the right and leaks background in on the left. Still rounded to
# even, because crop and overlay offsets on yuv420p must be.
def _even_pos(value: float) -> int:
    return max(int(round(value / 2.0)) * 2, 0)


@dataclass(frozen=True)
class Box:
    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True)
class Geometry:
    canvas_w: int
    canvas_h: int
    # The source video's own pixels, after any blur and before compositing.
    video: Box
    # In source-frame pixels: a blur is placed against the thing it hides, not
    # against the canvas it later lands on.
    blur_regions: list[Box] = field(default_factory=list)
    logo: Box | None = None
    logo_opacity: float = 1.0


def resolve(preset: dict, source_w: int, source_h: int) -> Geometry:
    canvas = preset.get("canvas") or {}
    canvas_w = _even(canvas.get("w", 1080))
    canvas_h = _even(canvas.get("h", 1920))

    rect = preset.get("video_rect") or {"x": 0.0, "y": 0.25, "w": 1.0, "h": 0.5}
    slot_w = _even(canvas_w * float(rect["w"]))
    slot_h = _even(canvas_h * float(rect["h"]))

    # Fitted inside the slot rather than stretched to it: the slot's aspect
    # ratio is a layout choice, the source's is not negotiable, and stretching
    # a 16:9 source into a 1:1 slot is instantly visible on faces.
    scale = min(slot_w / source_w, slot_h / source_h)
    video_w = _even(source_w * scale)
    video_h = _even(source_h * scale)
    video_x = _even_pos(canvas_w * float(rect["x"]) + (slot_w - video_w) / 2)
    video_y = _even_pos(canvas_h * float(rect["y"]) + (slot_h - video_h) / 2)

    blur_regions = clamp_regions(
        [
            Box(
                x=_even_pos(source_w * float(region["x"])),
                y=_even_pos(source_h * float(region["y"])),
                w=_even(source_w * float(region["w"])),
                h=_even(source_h * float(region["h"])),
            )
            for region in preset.get("blur_regions") or []
        ],
        source_w,
        source_h,
    )

    logo_box = None
    logo_opacity = 1.0
    logo = preset.get("logo")
    if logo:
        logo_w = _even(canvas_w * float(logo["w"]))
        logo_box = Box(
            x=_even_pos(canvas_w * float(logo["x"])),
            y=_even_pos(canvas_h * float(logo["y"])),
            w=logo_w,
            # -1 keeps the logo's own aspect ratio; ffmpeg fills it in.
            h=-1,
        )
        logo_opacity = float(logo.get("opacity", 1.0))

    return Geometry(
        canvas_w=canvas_w,
        canvas_h=canvas_h,
        video=Box(x=video_x, y=video_y, w=video_w, h=video_h),
        blur_regions=blur_regions,
        logo=logo_box,
        logo_opacity=logo_opacity,
    )


def on_canvas(region: dict, geometry: Geometry) -> Box | None:
    """Where a source-normalised rectangle lands once the footage is placed.

    `resolve` fits the whole source inside `geometry.video`, so a fraction of
    the source is the same fraction of that box — which is what lets a blur
    keep source coordinates (it hides something in the footage) and still be
    composited on the canvas at its own place in the layer stack.

    Returns None for a rectangle that ends up outside the canvas: a crop that
    reaches past the frame fails the whole render rather than blurring what it
    can reach.
    """
    video = geometry.video
    left = _even_pos(video.x + video.w * float(region["x"]))
    top = _even_pos(video.y + video.h * float(region["y"]))
    right = min(left + _even(video.w * float(region["w"])), geometry.canvas_w)
    bottom = min(top + _even(video.h * float(region["h"])), geometry.canvas_h)
    # Rounded *down* here, unlike a standalone dimension: rounding 1079 up to
    # 1080 against a 1080 canvas puts the crop one pixel past the edge.
    width = int((right - left) // 2) * 2
    height = int((bottom - top) // 2) * 2
    if width < 2 or height < 2:
        return None
    return Box(x=left, y=top, w=width, h=height)


def clamp_regions(regions: list[Box], source_w: int, source_h: int) -> list[Box]:
    """Keep every blur box inside the frame.

    A region written slightly over the edge — 0.02 + 0.30 against a source that
    is narrower than the one it was drawn on — makes ffmpeg reject the whole
    crop rather than blur what it can.
    """
    clamped = []
    for region in regions:
        # The intersection with the frame, not a clamp of the origin: clamping
        # x to `source_w - 2` turns a box that misses the frame entirely into a
        # 2-pixel sliver blurred in the corner, which is a visible artefact
        # standing in for a region that should simply not be drawn.
        left = max(region.x, 0)
        top = max(region.y, 0)
        right = min(region.x + region.w, source_w)
        bottom = min(region.y + region.h, source_h)
        width = _even(right - left)
        height = _even(bottom - top)
        if right - left >= 2 and bottom - top >= 2:
            clamped.append(Box(x=left, y=top, w=width, h=height))
    return clamped


@lru_cache(maxsize=16)
def font_file(family: str) -> str:
    """Resolve a family name from the preset to a file on disk.

    Through fontconfig rather than a hardcoded path, so a preset can name any
    family the image has installed. fontconfig always answers with *something*,
    so a missing family is a substitution, not a crash — the render still
    completes and the subtitles are simply in the wrong face.
    """
    result = subprocess.run(
        ["fc-match", "-f", "%{file}", family],
        check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()
