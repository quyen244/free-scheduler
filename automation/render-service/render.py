"""The ffmpeg half: blur, composite, logo, watermark, burned subtitles.

One filtergraph per chunk, built from the preset's geometry. Filenames inside
a filtergraph are read by ffmpeg's own parser, where `:` separates options and
`\\` escapes — a Windows-shaped or space-carrying path turns into a syntax
error rather than a missing file. So every command here runs with its working
directory set to the chunk folder and names its files relatively.
"""

import json
import logging
import math
import shutil
import subprocess
import time
from array import array
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import library
import manifest as manifests
import preset as presets
import visual_preset
import brand as brands
import subs
from config import settings
from errors import RenderError

logger = logging.getLogger(__name__)

SUBS_NAME = "subs.ass"
CLIP_NAME = "final.mp4"
CONCAT_NAME = "final.mp4"


@dataclass(frozen=True)
class Source:
    width: int
    height: int
    duration_s: float
    # Keep generated canvas frames in lockstep with the source. A fixed 30 fps
    # canvas duplicates frames for common 23.976/25 fps sources, making every
    # CPU filter and the encoder process work that cannot improve the result.
    frame_rate: str = "30"


def probe(path: Path) -> Source:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,r_frame_rate:format=duration",
            "-of", "json", str(path),
        ],
        check=True, capture_output=True, text=True,
    )
    data = json.loads(result.stdout)
    stream = data["streams"][0]
    return Source(
        width=int(stream["width"]),
        height=int(stream["height"]),
        duration_s=float(data["format"]["duration"]),
        frame_rate=_valid_frame_rate(str(stream.get("r_frame_rate") or "30")),
    )


def _valid_frame_rate(value: str) -> str:
    """Return an ffmpeg-safe positive source frame rate, with a stable fallback."""
    try:
        numerator, denominator = (
            (int(part) for part in value.split("/", 1))
            if "/" in value
            else (int(value), 1)
        )
    except ValueError:
        return "30"
    return value if numerator > 0 and denominator > 0 else "30"


PREFERRED_ENCODER = "h264_nvenc"
FALLBACK_ENCODER = "libx264"

# NVENC refuses frames below roughly 145x49 with "Frame Dimension less than the
# minimum supported value", so a probe canvas has to clear that bar or it fails
# on a perfectly working GPU and the whole pipeline silently drops to the CPU.
# 256x256 is comfortably above the floor and still costs a single frame.
_PROBE_SIZE = "256x256"


@dataclass(frozen=True)
class EncoderChoice:
    """Which encoder was asked for, which one runs, and why they differ."""

    requested: str
    selected: str
    fallback_reason: str | None

    @property
    def is_hardware(self) -> bool:
        return self.selected == PREFERRED_ENCODER

    def as_dict(self) -> dict[str, object]:
        return {
            "requested_encoder": self.requested,
            "selected_encoder": self.selected,
            "hardware": self.is_hardware,
            "fallback_reason": self.fallback_reason,
        }


@lru_cache(maxsize=1)
def encoder_choice() -> EncoderChoice:
    """Pick the h264 encoder once, by trying it rather than by asking.

    `ffmpeg -encoders` lists h264_nvenc whenever the binary was built with it,
    which says nothing about whether this container can reach a GPU. A real
    encode is the only answer that is not a guess.

    The probe uses the same encoder arguments production does, so an argument
    the GPU rejects is discovered here rather than minutes into a render. When
    it fails, the ffmpeg error is kept and reported: a CPU fallback that costs
    hours must never be invisible.
    """
    probe_command = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=black:s={_PROBE_SIZE}:d=0.1",
        *_encoder_args(PREFERRED_ENCODER, {}), "-pix_fmt", "yuv420p",
        "-f", "null", "-",
    ]
    try:
        subprocess.run(probe_command, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        reason = f"ffmpeg binary is not on PATH: {exc}"
    except subprocess.CalledProcessError as exc:
        reason = _fallback_reason(exc.stderr or "")
    else:
        logger.info(
            "encoder probe: selected=%s hardware=True",
            PREFERRED_ENCODER,
        )
        return EncoderChoice(PREFERRED_ENCODER, PREFERRED_ENCODER, None)

    logger.warning(
        "encoder probe: requested=%s unusable, falling back to selected=%s "
        "hardware=False reason=%s",
        PREFERRED_ENCODER, FALLBACK_ENCODER, reason,
    )
    return EncoderChoice(PREFERRED_ENCODER, FALLBACK_ENCODER, reason)


def _fallback_reason(stderr: str) -> str:
    """Classify why the hardware encoder is unusable, keeping ffmpeg's words.

    The classes are the ones that actually occur on this stack and each points
    at a different fix, so a typed reason is worth more than a raw dump.
    """
    text = stderr.strip()
    lowered = text.lower()
    if "libnvidia-encode" in lowered:
        code = "driver_encode_library_missing"
    elif "frame dimension" in lowered:
        code = "probe_dimensions_rejected"
    elif "unknown encoder" in lowered or "not found" in lowered:
        code = "encoder_not_built_into_ffmpeg"
    elif "no capable devices" in lowered or "no such device" in lowered:
        code = "no_gpu_visible_to_container"
    elif "out of memory" in lowered:
        code = "gpu_out_of_memory"
    else:
        code = "hardware_encoder_open_failed"
    detail = " | ".join(line for line in text.splitlines() if line.strip())
    return f"{code}: {detail[-400:]}" if detail else code


def _encoder_args(name: str, config: dict) -> list[str]:
    quality = str(config.get("crf", 21))
    if name == PREFERRED_ENCODER:
        return ["-c:v", PREFERRED_ENCODER, "-preset", "p4", "-cq", quality]
    return ["-c:v", FALLBACK_ENCODER, "-preset", "veryfast", "-crf", quality]


def encoder() -> str:
    """The encoder that will actually run. Kept for existing job payloads."""
    return encoder_choice().selected


def _encode_args(config: dict) -> list[str]:
    return _encoder_args(encoder_choice().selected, config)


def _run_encode(
    command: list[str],
    *,
    stage: str,
    content_item_id: str,
    media_duration_s: float,
    cwd: Path | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> None:
    """Run one encode, recording which encoder ran and how long it took.

    A stage that costs minutes has to say so in the log with the encoder that
    produced it. Without that line a CPU fallback and a slow filtergraph look
    identical from the outside, and the speed multiple is what separates them.
    """
    choice = encoder_choice()
    started = time.monotonic()
    logger.info(
        "encode start: stage=%s item=%s media_duration_s=%.3f "
        "requested_encoder=%s selected_encoder=%s hardware=%s fallback_reason=%s",
        stage, content_item_id, media_duration_s,
        choice.requested, choice.selected, choice.is_hardware, choice.fallback_reason,
    )
    try:
        if cancelled is None:
            subprocess.run(command, check=True, capture_output=True, text=True, cwd=cwd)
        else:
            process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=cwd
            )
            while process.poll() is None:
                if cancelled():
                    process.terminate()
                    _, stderr = process.communicate()
                    raise subprocess.CalledProcessError(process.returncode or -15, command, stderr=stderr)
                time.sleep(0.2)
            _, stderr = process.communicate()
            if process.returncode:
                raise subprocess.CalledProcessError(process.returncode, command, stderr=stderr)
    except subprocess.CalledProcessError:
        logger.error(
            "encode failed: stage=%s item=%s selected_encoder=%s elapsed_s=%.3f",
            stage, content_item_id, choice.selected, time.monotonic() - started,
        )
        raise
    elapsed = time.monotonic() - started
    logger.info(
        "encode done: stage=%s item=%s selected_encoder=%s elapsed_s=%.3f speed_x=%.2f",
        stage, content_item_id, choice.selected, elapsed,
        (media_duration_s / elapsed) if elapsed > 0 else 0.0,
    )


def _blur_chain(regions: list[presets.Box], source: Source) -> tuple[list[str], str]:
    """Hide each region behind a blur of itself, in source pixels."""
    if not regions:
        return [], "0:v"

    # Scaled to the frame: a fixed radius that hides a caption on a 1080p
    # source leaves it legible on a 4K one.
    radius = max(int(round(min(source.width, source.height) * 0.02)), 2)

    labels = "".join(f"[r{index}]" for index in range(len(regions)))
    steps = [f"[0:v]split={len(regions) + 1}[base]{labels}"]
    for index, region in enumerate(regions):
        steps.append(
            f"[r{index}]crop={region.w}:{region.h}:{region.x}:{region.y},"
            f"boxblur=luma_radius={radius}:luma_power=2[b{index}]"
        )
    current = "base"
    for index, region in enumerate(regions):
        nxt = f"s{index}"
        steps.append(f"[{current}][b{index}]overlay={region.x}:{region.y}[{nxt}]")
        current = nxt
    return steps, current


def _stacked_blur(
    region: presets.Box, geometry: presets.Geometry, stem: str
) -> Callable[[str, str], list[str]]:
    """Blur one rectangle of the picture the stack has drawn so far.

    The pre-stacking blur in `_blur_chain` works on the source's own pixels and
    is therefore always underneath everything. This one is an ordinary layer:
    it blurs whatever the layers below it painted, so an operator can hide a
    caption in the footage *and* the watermark somebody put over it.

    Three steps rather than one filter - copy the frame, blur the crop, put it
    back - which is why a layer contributes a callable instead of a string.
    """
    # Scaled to the canvas, matching `_blur_chain` scaling to the source: the
    # region covers the same picture either way, so it needs the same strength.
    radius = max(int(round(min(geometry.canvas_w, geometry.canvas_h) * 0.02)), 2)

    def build(current: str, nxt: str) -> list[str]:
        return [
            f"[{current}]split=2[{stem}_keep][{stem}_cut]",
            f"[{stem}_cut]crop={region.w}:{region.h}:{region.x}:{region.y},"
            f"boxblur=luma_radius={radius}:luma_power=2[{stem}_soft]",
            f"[{stem}_keep][{stem}_soft]overlay={region.x}:{region.y}[{nxt}]",
        ]

    return build


def _drawtext(config: dict, textfile: str, geometry: presets.Geometry, family: str) -> str:
    """One centred line of text.

    Through `textfile=` rather than `text=`: the strings here are Vietnamese
    channel names and LLM-written hooks, and a `:` or an apostrophe in one of
    those is a filtergraph parse error, not an escaped character.
    """
    size = int(config.get("size", 48))
    colour = str(config.get("color", "#FFFFFF")).lstrip("#")
    opacity = float(config.get("opacity", 1.0))
    y = int(round(geometry.canvas_h * float(config.get("y", 0.5))))
    return (
        f"drawtext=fontfile={presets.font_file(family)}:textfile={textfile}"
        f":fontsize={size}:fontcolor=0x{colour}@{opacity}"
        f":borderw={max(size // 16, 1)}:bordercolor=0x000000@{opacity}"
        f":x=(w-text_w)/2:y={y}"
    )


def _write_text(directory: Path, name: str, text: str) -> str:
    (directory / name).write_text(text, encoding="utf-8")
    return name


# Input slots shared by every clean render. The source, the background still
# and the Vietnamese voice are always present, so their indices are fixed and
# anything optional is appended after them.
_BACKGROUND_INPUT = 1
_VOICE_INPUT = 2
_FIRST_OPTIONAL_INPUT = 3


def _background_args(
    preset: dict, geometry: presets.Geometry, duration_s: float, frame_rate: str = "30"
) -> list[str]:
    """Input 1: the still behind everything, or a black canvas when unset.

    A generated black frame rather than no input at all, so the index of every
    later input stays the same whether or not the preset carries a background.
    """
    name = str(preset.get("background") or "")
    if not name:
        return [
            "-f", "lavfi",
            "-t", f"{duration_s:.3f}",
            "-i", (
                f"color=c=black:s={geometry.canvas_w}x{geometry.canvas_h}:"
                f"r={_valid_frame_rate(frame_rate)}"
            ),
        ]
    return [
        "-loop", "1",
        "-t", f"{duration_s:.3f}",
        "-i", str(library.background_path(name)),
    ]


def _canvas_box(rect: dict, canvas_w: int, canvas_h: int) -> presets.Box:
    """A normalised rectangle in canvas pixels, kept even for yuv420p."""
    return presets.Box(
        x=int(round(canvas_w * float(rect.get("x", 0.0)))),
        y=int(round(canvas_h * float(rect.get("y", 0.0)))),
        w=max(int(round(canvas_w * float(rect.get("w", 1.0)))) // 2 * 2, 2),
        h=max(int(round(canvas_h * float(rect.get("h", 1.0)))) // 2 * 2, 2),
    )


def _text_step(
    layer: dict,
    value: str,
    geometry: presets.Geometry,
    directory: Path,
    prefix: str,
    family: str,
) -> str:
    """One text layer, wrapped and aligned inside its own rectangle.

    drawtext has no word wrap, so a long title would run straight off the
    canvas. Wrapping happens here, through the same function the subtitles
    use, and the result is written to a file rather than inlined: these
    strings are Vietnamese, and a colon or an apostrophe inside a filtergraph
    is a parse error rather than a character.
    """
    max_lines = max(int(layer.get("max_lines", 3)), 1)
    lines = subs.wrap(value, max(int(layer.get("max_chars_per_line", 28)), 6))[:max_lines]
    layer_id = str(layer.get("id", "text"))
    textfile = _write_text(
        directory, f"{prefix}.text-{layer_id}.txt", "\n".join(lines)
    )

    size = max(int(layer.get("size", 48)), 8)
    colour = str(layer.get("color", "#FFFFFF")).lstrip("#")
    left = int(round(geometry.canvas_w * float(layer.get("x", 0.0))))
    width = int(round(geometry.canvas_w * float(layer.get("w", 1.0))))
    top = int(round(geometry.canvas_h * float(layer.get("y", 0.0))))
    align = str(layer.get("align", "center"))
    if align == "left":
        x = str(left)
    elif align == "right":
        x = f"{left + width}-text_w"
    else:
        x = f"{left}+({width}-text_w)/2"
    spacing = max(int(round(size * (float(layer.get("line_spacing", 1.15)) - 1.0))), 0)
    return (
        f"drawtext=fontfile={presets.font_file(family)}:textfile={textfile}"
        f":fontsize={size}:fontcolor=0x{colour}:line_spacing={spacing}"
        f":borderw={max(size // 16, 1)}:bordercolor=0x000000"
        f":x={x}:y={top}"
    )


def _host_layer(
    host: dict, geometry: presets.Geometry, duration_s: float, first_input: int
) -> tuple[list[str], list[str], str]:
    """The matted presenter: its own video plus the RVM alpha, merged.

    The alpha revision is required rather than optional. Without it the host
    composites as an opaque rectangle sitting on the layout, which is not a
    slightly worse render but an obviously broken one, and refusing here is
    cheaper than discovering it in a published video.
    """
    if host.get("path"):
        # A brand carries the presenter and its mask as two files in its own
        # folder, already paired at upload time, so there is nothing to resolve.
        video_path = Path(str(host["path"]))
        alpha = Path(str(host.get("alpha_path") or ""))
    else:
        revision = str(host.get("alpha_revision") or "")
        if not revision:
            raise RenderError(
                "the preset places a host but carries no RVM alpha revision; "
                "run matting and publish a new revision before rendering"
            )
        video_path = library.asset_path(str(host["asset"]))
        alpha = visual_preset.alpha_mask_path(str(host["preset_id"]), revision)
    if not alpha.is_file():
        raise RenderError(f"host alpha mask is missing at {alpha}")

    video_index, alpha_index = first_input, first_input + 1
    box = _canvas_box(host.get("rect") or {}, geometry.canvas_w, geometry.canvas_h)
    args = [
        # Looped, so a host shorter than the chunk keeps presenting instead of
        # freezing on its last frame for the remainder.
        "-stream_loop", "-1", "-t", f"{duration_s:.3f}",
        "-i", str(video_path),
        "-stream_loop", "-1", "-t", f"{duration_s:.3f}",
        "-i", str(alpha),
    ]
    steps = [
        f"[{video_index}:v]scale={box.w}:{box.h},setsar=1,format=gbrp[hostrgb]",
        f"[{alpha_index}:v]scale={box.w}:{box.h},setsar=1,format=gray[hostalpha]",
        "[hostrgb][hostalpha]alphamerge[hostrgba]",
    ]
    return args, steps, f"overlay={box.x}:{box.y}:eof_action=pass"


def _image_layer(
    image: dict, geometry: presets.Geometry, index: int
) -> tuple[list[str], list[str], str, str]:
    """One still placed in its own rectangle on the canvas.

    A brand's background, logo and watermark are all this: the old schema gave
    each its own named slot and its own code path, which is why adding a fourth
    still meant editing the renderer. Here they differ only by rectangle and z.
    """
    box = _canvas_box(image, geometry.canvas_w, geometry.canvas_h)
    label = f"img{index}"
    # One frame, not one per output frame. `-loop 1` made ffmpeg re-decode the
    # still and re-run its scale/alpha chain for every frame of the render:
    # a brand carrying a 1536x2752 background and a 2048x2048 watermark spent
    # 88 % of a measured 9:16 chunk doing that. `overlay` repeats its last
    # secondary frame for the rest of the main stream by default, so a single
    # decoded frame composites identically - measured SSIM 1.000000 on every
    # frame of a 20-second chunk - for 1/480th of the decode and scale work.
    args = ["-i", str(image["path"])]
    opacity = float(image.get("opacity", 1.0))
    if str(image.get("fit", "contain")) == "fill":
        scale = f"scale={box.w}:{box.h}"
        placement = f"{box.x}:{box.y}"
    else:
        # Fitted inside the rectangle and centred in it, so a logo keeps its
        # own aspect ratio no matter what shape the operator drags around it.
        scale = f"scale={box.w}:{box.h}:force_original_aspect_ratio=decrease"
        placement = f"{box.x}+({box.w}-w)/2:{box.y}+({box.h}-h)/2"
    steps = [
        f"[{index}:v]{scale},setsar=1,format=rgba,"
        f"colorchannelmixer=aa={opacity}[{label}]"
    ]
    return args, steps, label, f"overlay={placement}"


def compose_clean(
    preset: dict,
    geometry: presets.Geometry,
    source: Source,
    directory: Path,
    prefix: str,
    subtitle_name: str,
    duration_s: float,
    fields: dict[str, str] | None = None,
    texts: dict[str, str] | None = None,
) -> tuple[str, list[str], list[str]]:
    """Build the brand-neutral half of a render.

    Returns the video filtergraph, the extra ffmpeg inputs it needs after the
    fixed three, and any warnings worth recording on the asset. Layers are
    emitted in ascending `z` so that the stacking order shown in the editor and
    the order ffmpeg composites in are the same fact rather than two
    descriptions that can disagree.
    """
    fields = fields or {}
    warnings: list[str] = []
    steps, video_label = _blur_chain(geometry.blur_regions, source)
    family = str((preset.get("subtitle") or {}).get("font", "DejaVu Sans"))

    video = geometry.video
    steps.append(f"[{video_label}]scale={video.w}:{video.h},setsar=1[vid]")
    steps.append(
        f"[{_BACKGROUND_INPUT}:v]scale={geometry.canvas_w}:{geometry.canvas_h},setsar=1[bg]"
    )

    extra_inputs: list[str] = []
    # (z, sequence, label stem, what it adds to the running composite: either a
    # filter to hang off it, or a builder for the layers that need more than one)
    layers: list[tuple[int, int, str, str | Callable[[str, str], list[str]]]] = []
    sequence = 0

    if preset.get("video_visible", True):
        layers.append(
            (
                int(preset.get("video_z", 10)),
                sequence,
                "main",
                f"[vid]overlay={video.x}:{video.y}",
            )
        )
    sequence += 1

    # Stills a brand places itself: background, logo, watermark, or anything
    # else it uploaded. Each is an ordinary z-sorted layer, which is what lets
    # an operator put a frame over the footage and the logo under it.
    for image in preset.get("images") or []:
        index = _FIRST_OPTIONAL_INPUT + extra_inputs.count("-i")
        args, image_steps, label, overlay = _image_layer(image, geometry, index)
        extra_inputs.extend(args)
        steps.extend(image_steps)
        layers.append(
            (
                int(image.get("z", 10)),
                sequence,
                f"img{index}",
                f"[{label}]{overlay}",
            )
        )
        sequence += 1

    # Blurs that were given a place in the stack. Their rectangles are still
    # source-normalised - they hide something in the footage - so they are
    # mapped through the footage's own box before they become canvas pixels.
    for blur in preset.get("blur_layers") or []:
        region = presets.on_canvas(blur, geometry)
        if region is None:
            warnings.append(
                f"blur layer {blur.get('id')!r} falls outside the canvas and was not drawn"
            )
            continue
        stem = f"blur{sequence}"
        layers.append(
            (int(blur.get("z", 11)), sequence, stem, _stacked_blur(region, geometry, stem))
        )
        sequence += 1

    host = preset.get("host")
    if host and (host.get("asset") or host.get("path")):
        first_input = _FIRST_OPTIONAL_INPUT + extra_inputs.count("-i")
        args, host_steps, overlay = _host_layer(host, geometry, duration_s, first_input)
        extra_inputs.extend(args)
        steps.extend(host_steps)
        layers.append((int(host.get("z", 20)), sequence, "host", f"[hostrgba]{overlay}"))
    sequence += 1

    for layer in preset.get("text_layers") or []:
        binding = str(layer.get("source", "static"))
        # Frozen service-wide, not removed from the layout: the layer keeps its
        # place so the switch can be reversed without editing every brand. Both
        # names are checked because a title can be a bound field or a static
        # string that happens to be the title layer.
        if binding in settings.frozen_text or str(layer.get("id", "")) in settings.frozen_text:
            warnings.append(
                f"text layer {layer.get('id')!r} was not drawn: RENDER_FROZEN_TEXT "
                f"freezes {sorted(settings.frozen_text)}"
            )
            continue
        value = (
            str(layer.get("text") or "")
            if binding == "static"
            else str(fields.get(binding) or "")
        ).strip()
        if not value:
            # A bound field with nothing behind it is reported rather than
            # quietly replaced by the editor preview string, which would put
            # placeholder text into a delivery asset.
            if binding != "static":
                warnings.append(
                    f"text layer {layer.get('id')!r} is bound to {binding!r} "
                    f"and was skipped because that field is empty"
                )
            continue
        layers.append(
            (
                int(layer.get("z", 50)),
                sequence,
                f"text{sequence}",
                _text_step(layer, value, geometry, directory, prefix, family),
            )
        )
        sequence += 1

    # The per-chunk hook and caption boxes of the pre-editor presets. They stay
    # supported so a job that names no editor preset still renders.
    for key in ("caption_top", "caption_bottom"):
        config = preset.get(key)
        value = str((texts or {}).get(key, "")).strip()
        if not config or not value:
            continue
        textfile = _write_text(directory, f"{prefix}.{key}.txt", value)
        layers.append(
            (
                int(config.get("z", 50)),
                sequence,
                key,
                _drawtext(config, textfile, geometry, family),
            )
        )
        sequence += 1

    subtitle = preset.get("subtitle") or {}
    # `and subtitle`, not just the visible flag: a brand that placed no
    # subtitle layer sends nothing here, and an empty dict defaulting to
    # visible would burn subtitles into a layout that never asked for them.
    if subtitle and subtitle.get("visible", True):
        layers.append(
            (int(subtitle.get("z", 60)), sequence, "subs", f"ass={subtitle_name}")
        )

    current = "bg"
    for _, _, stem, fragment in sorted(layers, key=lambda item: (item[0], item[1])):
        nxt = f"{stem}_out"
        if callable(fragment):
            steps.extend(fragment(current, nxt))
        else:
            steps.append(f"[{current}]{fragment}[{nxt}]")
        current = nxt
    if current == "bg":
        # Drawing nothing at all is a configuration mistake rather than a
        # crash, but the graph still has to produce a stream to map.
        steps.append("[bg]null[vout]")
        warnings.append("preset draws no visible layer over its background")
    else:
        steps[-1] = steps[-1].rsplit("[", 1)[0] + "[vout]"
    return ";".join(steps), extra_inputs, warnings


def build_filtergraph(
    preset: dict,
    geometry: presets.Geometry,
    source: Source,
    directory: Path,
    texts: dict[str, str],
) -> str:
    steps, video_label = _blur_chain(geometry.blur_regions, source)
    box = geometry.video

    steps.append(f"[{video_label}]scale={box.w}:{box.h},setsar=1[vid]")
    steps.append(f"[1:v]scale={geometry.canvas_w}:{geometry.canvas_h},setsar=1[bg]")
    steps.append(f"[bg][vid]overlay={box.x}:{box.y}[comp]")
    current = "comp"

    if geometry.logo is not None:
        steps.append(
            f"[2:v]scale={geometry.logo.w}:-1,format=rgba,"
            f"colorchannelmixer=aa={geometry.logo_opacity}[logo]"
        )
        steps.append(f"[{current}][logo]overlay={geometry.logo.x}:{geometry.logo.y}[withlogo]")
        current = "withlogo"

    family = str((preset.get("subtitle") or {}).get("font", "DejaVu Sans"))

    # The watermark's text is a channel constant and lives in the preset; the
    # caption boxes are per chunk and are empty until F8 writes a hook and a
    # caption into the row. A box with no text is simply not drawn.
    watermark = preset.get("watermark") or {}
    drawn = {
        "watermark": str(watermark.get("text", "")),
        "caption_top": texts.get("caption_top", ""),
        "caption_bottom": texts.get("caption_bottom", ""),
    }

    for key, text in drawn.items():
        config = preset.get(key)
        if not config or not text.strip():
            continue
        textfile = _write_text(directory, f"{key}.txt", text.strip())
        nxt = f"{key}_out"
        steps.append(f"[{current}]{_drawtext(config, textfile, geometry, family)}[{nxt}]")
        current = nxt

    steps.append(f"[{current}]ass={SUBS_NAME}[vout]")
    # apad, so a chunk that runs past the end of the voice track still gets
    # audio for its whole length instead of ffmpeg cutting the picture short at
    # the last spoken word. apad never ends on its own, so the caller bounds the
    # output with -t; -shortest cannot do it, see the note on the command.
    steps.append("[3:a]apad[aout]")
    return ";".join(steps)


def render_chunk(
    video_id: str,
    chunk: dict,
    preset: dict,
    segments: list[dict],
    texts: dict[str, str] | None = None,
) -> dict[str, object]:
    """Cut, dress and encode one chunk. Returns what it wrote."""
    raw = library.raw_path(video_id)
    voice = library.load_voice_track(video_id)
    source = probe(raw)

    geometry = presets.resolve(preset, source.width, source.height)

    idx = int(chunk["idx"])
    start_s = float(chunk["start_s"])
    duration_s = float(chunk["end_s"]) - start_s

    directory = library.chunk_dir(video_id, idx)
    directory.mkdir(parents=True, exist_ok=True)

    within = segments_within(segments, start_s, float(chunk["end_s"]))
    subs.write(
        directory / SUBS_NAME,
        subs.build(within, preset.get("subtitle") or {}, geometry.canvas_w, geometry.canvas_h, start_s),
    )

    background = library.background_path(str(preset.get("background", "")))
    logo = library.asset_path(str((preset.get("logo") or {}).get("path", "logo.png")))
    filtergraph = build_filtergraph(preset, geometry, source, directory, texts or {})

    output = directory / CLIP_NAME
    partial = directory / (CLIP_NAME + ".part.mp4")
    command = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", f"{start_s:.3f}", "-t", f"{duration_s:.3f}", "-i", str(raw),
        "-loop", "1", "-t", f"{duration_s:.3f}", "-i", str(background),
        "-i", str(logo),
        "-ss", f"{start_s:.3f}", "-t", f"{duration_s:.3f}", "-i", str(voice),
        "-filter_complex", filtergraph,
        "-map", "[vout]", "-map", "[aout]",
        *_encode_args(preset.get("encode") or {}),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", str((preset.get("encode") or {}).get("abr", "128k")),
        # -t, not -shortest. apad is an endless source, and ffmpeg 6.1 answers
        # that pairing by overrunning the span and then failing the command with
        # ENOSPC ("No space left on device") after the file is already written.
        # An explicit length ends the padding exactly where the chunk ends.
        "-t", f"{duration_s:.3f}",
        "-movflags", "+faststart",
        str(partial),
    ]

    try:
        subprocess.run(command, check=True, capture_output=True, text=True, cwd=directory)
    except subprocess.CalledProcessError as exc:
        partial.unlink(missing_ok=True)
        raise RenderError(f"chunk {idx} of {video_id}: {exc.stderr.strip()[-800:]}") from exc

    partial.replace(output)
    rendered = probe(output)
    return {
        "idx": idx,
        "final_path": str(output),
        "duration_s": round(rendered.duration_s, 3),
        "width": rendered.width,
        "height": rendered.height,
        "bytes": output.stat().st_size,
    }


def render_clean_whole(
    video_id: str,
    render_revision: int,
    preset: dict,
    segments: list[dict],
    fields: dict[str, str] | None = None,
    warnings: list[str] | None = None,
) -> manifests.MediaAsset:
    """Render the complete source directly to the clean YouTube asset.

    This deliberately does not concatenate vertical chunks. The source video
    is scaled once into a 1920x1080 canvas, the Vietnamese voice replaces the
    source audio, and subtitles are burned against the whole timeline.
    Branding and signature music are later derivations of this clean master.
    """
    canvas = preset.get("canvas") or {}
    if (int(canvas.get("w", 0)), int(canvas.get("h", 0))) != (1920, 1080):
        raise RenderError("clean whole preset must use a 1920x1080 canvas")

    raw = library.raw_path(video_id)
    voice = library.load_voice_track(video_id)
    source = probe(raw)
    output = manifests.expected_asset_path(
        video_id, render_revision, "clean_whole", manifests.WHOLE_ITEM
    )
    output.parent.mkdir(parents=True, exist_ok=True)

    subtitle_name = "whole-16x9.subs.ass"
    subs.write(
        output.parent / subtitle_name,
        subs.build(
            segments,
            preset.get("subtitle") or {},
            1920,
            1080,
        ),
    )

    geometry = presets.resolve(preset, source.width, source.height)
    video_graph, extra_inputs, composed_warnings = compose_clean(
        preset,
        geometry,
        source,
        output.parent,
        "whole-16x9",
        subtitle_name,
        source.duration_s,
        fields=fields,
    )
    filtergraph = f"{video_graph};[{_VOICE_INPUT}:a]apad[aout]"

    partial = output.with_name("whole-16x9.part.mp4")
    command = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(raw),
        *_background_args(preset, geometry, source.duration_s, source.frame_rate),
        "-i",
        str(voice),
        *extra_inputs,
        "-filter_complex",
        filtergraph,
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        *_encode_args(preset.get("encode") or {}),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        str((preset.get("encode") or {}).get("abr", "192k")),
        "-t",
        f"{source.duration_s:.3f}",
        "-movflags",
        "+faststart",
        str(partial),
    ]

    try:
        _run_encode(
            command,
            stage="clean_whole",
            content_item_id=manifests.WHOLE_ITEM,
            media_duration_s=source.duration_s,
            cwd=output.parent,
        )
    except subprocess.CalledProcessError as exc:
        partial.unlink(missing_ok=True)
        raise RenderError(
            f"clean whole render of {video_id}: {exc.stderr.strip()[-800:]}"
        ) from exc

    partial.replace(output)
    return manifests.inspect_expected_asset(
        video_id,
        render_revision,
        "clean_whole",
        manifests.WHOLE_ITEM,
        expected_duration_s=source.duration_s,
        warnings=[*(warnings or []), *composed_warnings],
    )


def render_clean_vertical(
    video_id: str,
    render_revision: int,
    chunk: dict,
    preset: dict,
    segments: list[dict],
    texts: dict[str, str] | None = None,
    fields: dict[str, str] | None = None,
    warnings: list[str] | None = None,
) -> manifests.MediaAsset:
    """Render one brand-neutral, revisioned 1080x1920 chunk master."""
    canvas = preset.get("canvas") or {}
    if (int(canvas.get("w", 0)), int(canvas.get("h", 0))) != (1080, 1920):
        raise RenderError("clean vertical preset must use a 1080x1920 canvas")
    # A clean master is shared by every brand, so no brand file may reach it.
    # The preset still carries the logo and watermark rectangles; those are
    # geometry, and the branded stage is where a brand file lands in them.
    if preset.get("logo") or preset.get("watermark"):
        raise RenderError("clean vertical preset cannot contain brand logo or watermark")

    idx = int(chunk["idx"])
    content_item_id = str(chunk.get("name") or f"part_{idx + 1}")
    expected_name = f"part_{idx + 1}"
    if content_item_id != expected_name:
        raise RenderError(
            f"chunk index {idx} must be named {expected_name!r}, not {content_item_id!r}"
        )

    start_s = float(chunk["start_s"])
    end_s = float(chunk["end_s"])
    duration_s = end_s - start_s
    if duration_s <= 0:
        raise RenderError(f"{content_item_id} has a non-positive duration")

    raw = library.raw_path(video_id)
    voice = library.load_voice_track(video_id)
    source = probe(raw)
    geometry = presets.resolve(preset, source.width, source.height)
    output = manifests.expected_asset_path(
        video_id, render_revision, "clean_vertical", content_item_id
    )
    output.parent.mkdir(parents=True, exist_ok=True)

    subtitle_name = f"{content_item_id}.subs.ass"
    within = segments_within(segments, start_s, end_s)
    subs.write(
        output.parent / subtitle_name,
        subs.build(
            within,
            preset.get("subtitle") or {},
            geometry.canvas_w,
            geometry.canvas_h,
            start_s,
        ),
    )

    video_graph, extra_inputs, composed_warnings = compose_clean(
        preset,
        geometry,
        source,
        output.parent,
        content_item_id,
        subtitle_name,
        duration_s,
        # `part` needs no metadata lookup: a chunk knows which part it is.
        fields={"part": str(idx + 1), **(fields or {})},
        texts=texts,
    )
    filtergraph = f"{video_graph};[{_VOICE_INPUT}:a]apad[aout]"

    partial = output.with_name(f"{content_item_id}-9x16.part.mp4")
    command = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-ss",
        f"{start_s:.3f}",
        "-t",
        f"{duration_s:.3f}",
        "-i",
        str(raw),
        *_background_args(preset, geometry, duration_s, source.frame_rate),
        "-ss",
        f"{start_s:.3f}",
        "-t",
        f"{duration_s:.3f}",
        "-i",
        str(voice),
        *extra_inputs,
        "-filter_complex",
        filtergraph,
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        *_encode_args(preset.get("encode") or {}),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        str((preset.get("encode") or {}).get("abr", "128k")),
        "-t",
        f"{duration_s:.3f}",
        "-movflags",
        "+faststart",
        str(partial),
    ]
    try:
        _run_encode(
            command,
            stage="clean_vertical",
            content_item_id=content_item_id,
            media_duration_s=duration_s,
            cwd=output.parent,
        )
    except subprocess.CalledProcessError as exc:
        partial.unlink(missing_ok=True)
        raise RenderError(
            f"clean vertical render of {video_id}/{content_item_id}: "
            f"{exc.stderr.strip()[-800:]}"
        ) from exc

    partial.replace(output)
    return manifests.inspect_expected_asset(
        video_id,
        render_revision,
        "clean_vertical",
        content_item_id,
        expected_duration_s=duration_s,
        warnings=[*(warnings or []), *composed_warnings],
    )


_MUSIC_ANALYSIS_RATE = 16_000
_MUSIC_ENVELOPE_RATE = 50


def _write_music_peak_envelope(
    music_path: Path,
    output: Path,
    duration_s: float,
    volume_db: float,
    peak_control: dict,
) -> None:
    """Write a small gain track that limits music RMS without reading the voice.

    Standard FFmpeg compressors were measured on this track and either missed
    its musical swell or lowered the ordinary bed too. We therefore measure the
    post-gain music in fixed windows and feed a 50 Hz control track to
    ``amultiply``. Attack applies immediately; release is eased, so the bed
    does not jump back up after a loud bar.
    """
    window_s = int(peak_control["window_ms"]) / 1000.0
    window_samples = max(1, round(window_s * _MUSIC_ANALYSIS_RATE))
    decoded = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-stream_loop", "-1", "-i", str(music_path),
            "-af", f"volume={volume_db}dB", "-t", f"{duration_s:.3f}",
            "-ac", "1", "-ar", str(_MUSIC_ANALYSIS_RATE), "-f", "f32le", "-",
        ],
        check=True,
        capture_output=True,
    ).stdout
    samples = array("f")
    samples.frombytes(decoded)
    target = float(peak_control["target_rms"])
    attack_s = float(peak_control["attack_ms"]) / 1000.0
    release_s = float(peak_control["release_ms"]) / 1000.0
    per_window = max(1, round(window_s * _MUSIC_ENVELOPE_RATE))
    gain = 1.0
    values = array("f")
    for start in range(0, len(samples), window_samples):
        frame = samples[start : start + window_samples]
        if not frame:
            continue
        rms = math.sqrt(sum(float(sample) ** 2 for sample in frame) / len(frame))
        wanted = min(1.0, target / rms) if rms > 0 else 1.0
        seconds = attack_s if wanted < gain else release_s
        alpha = min(1.0, 1.0 / max(seconds * _MUSIC_ENVELOPE_RATE, 1.0))
        for _ in range(per_window):
            gain += (wanted - gain) * alpha
            values.append(gain)
    expected = math.ceil(duration_s * _MUSIC_ENVELOPE_RATE)
    if len(values) < expected:
        values.extend([gain] * (expected - len(values)))
    output.write_bytes(values[:expected].tobytes())


def _music_graph(
    music: dict, duration_s: float, music_input: int, gain_input: int | None = None
) -> str:
    """Mix the brand's bed under the voice, with optional music-only control.

    The clean path has no music because a clean master is shared; here the bed
    belongs to the brand and the render is already brand-specific, so the mix
    happens in the same pass rather than in a second encode.
    """
    fade_in = min(float(music.get("fade_in_s", 0.0)), duration_s / 2)
    fade_out = min(float(music.get("fade_out_s", 0.0)), duration_s / 2)
    fade_out_start = max(duration_s - fade_out, 0)
    ducking = music.get("ducking") or {}
    stereo = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    music_tail = "asetpts=PTS-STARTPTS[music];"
    if gain_input is not None:
        music_tail = (
            "asetpts=PTS-STARTPTS[music_raw];"
            f"[{gain_input}:a]aresample=48000,pan=stereo|c0=c0|c1=c0,"
            f"atrim=0:{duration_s:.3f}[music_gain];"
            "[music_raw][music_gain]amultiply[music];"
        )
    return (
        f"[{_VOICE_INPUT}:a]{stereo},asetpts=PTS-STARTPTS,apad,"
        f"atrim=0:{duration_s:.3f},asplit=2[speech][sidechain];"
        f"[{music_input}:a]{stereo},volume={float(music['volume_db'])}dB,"
        f"atrim=0:{duration_s:.3f},"
        f"afade=t=in:st=0:d={fade_in:.3f},"
        f"afade=t=out:st={fade_out_start:.3f}:d={fade_out:.3f},"
        f"{music_tail}"
        f"[music][sidechain]sidechaincompress="
        f"threshold={float(ducking.get('threshold', 0.02)):.6f}:"
        f"ratio={float(ducking.get('ratio', 8)):.3f}:"
        f"attack={float(ducking.get('attack_ms', 20)):.3f}:"
        f"release={float(ducking.get('release_ms', 450)):.3f}[ducked];"
        "[speech][ducked]amix=inputs=2:duration=first:dropout_transition=0:"
        "normalize=0,alimiter=limit=0.98[aout]"
    )


def render_brand_variant(
    video_id: str,
    render_revision: int,
    config: dict,
    brand_id: str,
    segments: list[dict],
    *,
    chunk: dict | None = None,
    fields: dict[str, str] | None = None,
    texts: dict[str, str] | None = None,
    warnings: list[str] | None = None,
    output_path: Path | None = None,
    cancelled: Callable[[], bool] | None = None,
    schema_version: str | None = None,
) -> manifests.MediaAsset:
    """Render one delivery asset straight from the source for one brand.

    A `brand.v1` layout owns the rectangle the footage sits in, the blur
    regions and the subtitle, so there is no brand-neutral master to derive
    from: everything this brand asked for is composited in a single pass.
    `chunk` is `None` for the whole 16:9 asset and a chunk row for a legacy
    9:16 part. New delivery revisions render only the whole here, then use
    `cut_branded_landscape_chunk` so chunks are exact sections of that file.
    """
    whole = chunk is None
    role: manifests.AssetRole = "branded_whole" if whole else "branded_vertical"
    expected_canvas = (1920, 1080) if whole else (1080, 1920)
    canvas = config.get("canvas") or {}
    if (int(canvas.get("w", 0)), int(canvas.get("h", 0))) != expected_canvas:
        raise RenderError(
            f"{brand_id}/{role} needs a {expected_canvas[0]}x{expected_canvas[1]} "
            "canvas; the layout for that aspect does not have one"
        )

    raw = library.raw_path(video_id)
    voice = library.load_voice_track(video_id)
    source = probe(raw)

    if whole:
        content_item_id = manifests.WHOLE_ITEM
        start_s, duration_s = 0.0, source.duration_s
        within = segments
        stem = "whole-16x9"
    else:
        idx = int(chunk["idx"])
        content_item_id = str(chunk.get("name") or f"part_{idx + 1}")
        expected_name = f"part_{idx + 1}"
        if content_item_id != expected_name:
            raise RenderError(
                f"chunk index {idx} must be named {expected_name!r}, not {content_item_id!r}"
            )
        start_s = float(chunk["start_s"])
        duration_s = float(chunk["end_s"]) - start_s
        if duration_s <= 0:
            raise RenderError(f"{content_item_id} has a non-positive duration")
        within = segments_within(segments, start_s, duration_s + start_s)
        stem = f"{content_item_id}-9x16"
        fields = {"part": str(idx + 1), **(fields or {})}

    output = output_path or manifests.expected_asset_path(
        video_id, render_revision, role, content_item_id, brand_id
    )
    output.parent.mkdir(parents=True, exist_ok=True)

    # Only a layout that placed a subtitle layer gets a script; `compose_clean`
    # leaves the `ass=` filter out entirely when the brand placed none.
    subtitle_name = f"{content_item_id}.subs.ass"
    if config.get("subtitle"):
        subs.write(
            output.parent / subtitle_name,
            subs.build(within, config["subtitle"], *expected_canvas, start_s),
        )

    geometry = presets.resolve(config, source.width, source.height)
    video_graph, extra_inputs, composed_warnings = compose_clean(
        config,
        geometry,
        source,
        output.parent,
        stem,
        subtitle_name,
        duration_s,
        fields=fields,
        texts=texts,
    )

    music = config.get("signature_music")
    music_inputs: list[str] = []
    music_gain_path: Path | None = None
    if music:
        # Appended last so the indices `compose_clean` already handed out to
        # the brand's own stills stay correct.
        music_index = _FIRST_OPTIONAL_INPUT + extra_inputs.count("-i")
        music_inputs = ["-stream_loop", "-1", "-i", str(music["path"])]
        peak_control = music.get("peak_control")
        gain_index = None
        if peak_control:
            music_gain_path = output.parent / f".{stem}.music-gain.f32"
            _write_music_peak_envelope(
                Path(music["path"]),
                music_gain_path,
                duration_s,
                float(music["volume_db"]),
                peak_control,
            )
            gain_index = music_index + 1
            music_inputs.extend(
                [
                    "-f", "f32le", "-ar", str(_MUSIC_ENVELOPE_RATE), "-ac", "1",
                    "-i", str(music_gain_path),
                ]
            )
        audio_graph = _music_graph(music, duration_s, music_index, gain_index)
    else:
        audio_graph = f"[{_VOICE_INPUT}:a]apad[aout]"

    span = ["-ss", f"{start_s:.3f}", "-t", f"{duration_s:.3f}"] if not whole else []
    encode = config.get("encode") or {}
    partial = output.with_name(f".{stem}.part.mp4")
    command = [
        "ffmpeg", "-y", "-loglevel", "error",
        *span, "-i", str(raw),
        *_background_args(config, geometry, duration_s, source.frame_rate),
        *span, "-i", str(voice),
        *extra_inputs,
        *music_inputs,
        "-filter_complex", f"{video_graph};{audio_graph}",
        "-map", "[vout]",
        "-map", "[aout]",
        *_encode_args(encode),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", str(encode.get("abr", "192k" if whole else "128k")),
        "-t", f"{duration_s:.3f}",
        "-movflags", "+faststart",
        str(partial),
    ]
    try:
        _run_encode(
            command,
            stage=role,
            content_item_id=content_item_id,
            media_duration_s=duration_s,
            cwd=output.parent,
            cancelled=cancelled,
        )
    except subprocess.CalledProcessError as exc:
        partial.unlink(missing_ok=True)
        raise RenderError(
            f"brand render of {video_id}/{content_item_id} for {brand_id}: "
            f"{exc.stderr.strip()[-800:]}"
        ) from exc
    finally:
        if music_gain_path is not None:
            music_gain_path.unlink(missing_ok=True)

    partial.replace(output)
    return manifests.inspect_expected_asset(
        video_id,
        render_revision,
        role,
        content_item_id,
        expected_duration_s=duration_s,
        brand_id=brand_id,
        warnings=[*(warnings or []), *composed_warnings],
        path=output,
        schema_version=schema_version,
    )


def cut_branded_landscape_chunk(
    whole_asset: manifests.MediaAsset,
    chunk: dict,
    *,
    output_path: Path | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> manifests.MediaAsset:
    """Frame-accurately re-encode one 16:9 delivery chunk from its whole parent."""
    if whole_asset.role != "branded_whole":
        raise RenderError("a landscape chunk must be cut from a branded whole asset")

    idx = int(chunk["idx"])
    content_item_id = str(chunk.get("name") or f"part_{idx + 1}")
    if content_item_id != f"part_{idx + 1}":
        raise RenderError(
            f"chunk index {idx} must be named part_{idx + 1}, not {content_item_id!r}"
        )
    start_s = float(chunk["start_s"])
    duration_s = float(chunk["end_s"]) - start_s
    if start_s < 0 or duration_s <= 0:
        raise RenderError(f"{content_item_id} has an invalid cut span")
    if start_s + duration_s > whole_asset.probe.duration_s + 0.5:
        raise RenderError(f"{content_item_id} exceeds its branded whole parent duration")

    output = output_path or manifests.expected_asset_path(
        whole_asset.video_id,
        whole_asset.render_revision,
        "branded_landscape_chunk",
        content_item_id,
        whole_asset.brand_id,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(f".{content_item_id}.part.mp4")
    # Put -ss after the input: ffmpeg decodes from the beginning before cutting,
    # which is slower than keyframe copy but makes the persisted transcript
    # boundary the actual first frame of the delivered asset.
    command = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", whole_asset.path,
        "-ss", f"{start_s:.3f}", "-t", f"{duration_s:.3f}",
        "-map", "0:v:0", "-map", "0:a:0",
        "-vf", "setpts=PTS-STARTPTS", "-af", "asetpts=PTS-STARTPTS",
        *_encode_args({}),
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", "-avoid_negative_ts", "make_zero",
        str(partial),
    ]
    try:
        _run_encode(
            command,
            stage="branded_landscape_chunk",
            content_item_id=content_item_id,
            media_duration_s=duration_s,
            cwd=output.parent,
            cancelled=cancelled,
        )
    except subprocess.CalledProcessError as exc:
        partial.unlink(missing_ok=True)
        raise RenderError(
            f"landscape cut of {whole_asset.video_id}/{content_item_id}: "
            f"{exc.stderr.strip()[-800:]}"
        ) from exc
    partial.replace(output)
    return manifests.inspect_expected_asset(
        whole_asset.video_id,
        whole_asset.render_revision,
        "branded_landscape_chunk",
        content_item_id,
        expected_duration_s=duration_s,
        brand_id=whole_asset.brand_id,
        lineage_asset_id=whole_asset.asset_id,
        path=output,
        schema_version=manifests.SCHEMA_VERSION,
    )


def _brand_overlay(
    profile: brands.MockBrandProfile,
    slot_name: str,
    slots: dict,
    canvas_w: int,
    canvas_h: int,
    input_index: int,
) -> tuple[list[str], list[str], str] | None:
    """One brand image placed in the rectangle the preset reserved for it.

    Both halves have to be present. A brand with no logo file has nothing to
    draw, and a preset with no logo slot has nowhere to draw it; in either case
    the correct result is an unbranded-in-that-respect video rather than a
    guessed placement.
    """
    image: brands.BrandImage | None = getattr(profile, slot_name, None)
    slot = (slots or {}).get(slot_name)
    if image is None or not slot or not slot.get("visible", True):
        return None
    box = _canvas_box(slot, canvas_w, canvas_h)
    args = ["-i", str(brands.image_path(profile, image))]
    label = f"{slot_name}img"
    steps = [
        # -1 preserves the artwork aspect ratio: the slot width is the layout
        # decision, and stretching a logo to an arbitrary height is the kind of
        # thing a brand owner notices immediately.
        f"[{input_index}:v]scale={box.w}:-1,format=rgba,"
        f"colorchannelmixer=aa={image.opacity}[{label}]"
    ]
    return args, steps, f"[{label}]overlay={box.x}:{box.y}"


def render_branded_variant(
    clean_asset: manifests.MediaAsset,
    profile: brands.MockBrandProfile,
    brand_slots: dict | None = None,
    warnings: list[str] | None = None,
) -> manifests.MediaAsset:
    """Derive one brand-specific video from a validated clean master.

    This is the only stage that reads a brand file. The clean master stays
    brand-neutral so a single render can be dressed for several brands, and
    `brand_slots` carries the rectangles the visual preset reserved.

    The mock music settings are a quiet, looped bed with short fades. The clean
    narration is also used as the sidechain so music falls during speech and
    rises gently in gaps.
    """
    if clean_asset.role not in ("clean_whole", "clean_vertical"):
        raise RenderError("a branded variant must derive from a clean asset")

    branded_role: manifests.AssetRole = (
        "branded_whole"
        if clean_asset.role == "clean_whole"
        else "branded_vertical"
    )
    output = manifests.expected_asset_path(
        clean_asset.video_id,
        clean_asset.render_revision,
        branded_role,
        clean_asset.content_item_id,
        profile.brand_id,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    clean_path = Path(clean_asset.path)
    if not clean_path.is_file():
        raise RenderError(f"clean lineage asset is missing: {clean_path}")

    music = brands.music_path(profile)
    duration_s = clean_asset.probe.duration_s

    fade_in = min(profile.signature_music.fade_in_s, duration_s / 2)
    fade_out = min(profile.signature_music.fade_out_s, duration_s / 2)
    fade_out_start = max(duration_s - fade_out, 0)
    ducking = profile.signature_music.ducking

    # Input 0 is the clean master and input 1 is the music bed, so brand images
    # start at 2.
    brand_inputs: list[str] = []
    brand_steps: list[str] = []
    overlays: list[str] = []
    brand_warnings: list[str] = []
    for slot_name in ("logo", "watermark"):
        placed = _brand_overlay(
            profile,
            slot_name,
            brand_slots or {},
            clean_asset.probe.width,
            clean_asset.probe.height,
            2 + brand_inputs.count("-i"),
        )
        if placed is None:
            brand_warnings.append(
                f"{profile.brand_id} has no {slot_name} to place, or the preset "
                f"reserves no {slot_name} slot; none was drawn"
            )
            continue
        args, steps, overlay = placed
        brand_inputs.extend(args)
        brand_steps.extend(steps)
        overlays.append(overlay)

    current = "0:v"
    for index, overlay in enumerate(overlays):
        nxt = "vout" if index == len(overlays) - 1 else f"brand{index}"
        brand_steps.append(f"[{current}]{overlay}[{nxt}]")
        current = nxt
    if not overlays:
        brand_steps.append("[0:v]null[vout]")

    filtergraph = (
        ";".join(brand_steps) + ";"
        "[0:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
        "asetpts=PTS-STARTPTS,asplit=2[speech][sidechain];"
        f"[1:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
        f"volume={profile.signature_music.volume_db}dB,atrim=0:{duration_s:.3f},"
        f"afade=t=in:st=0:d={fade_in:.3f},"
        f"afade=t=out:st={fade_out_start:.3f}:d={fade_out:.3f},"
        "asetpts=PTS-STARTPTS[music];"
        f"[music][sidechain]sidechaincompress=threshold={ducking.threshold:.6f}:"
        f"ratio={ducking.ratio:.3f}:attack={ducking.attack_ms:.3f}:"
        f"release={ducking.release_ms:.3f}[ducked];"
        "[speech][ducked]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
        "alimiter=limit=0.98[aout]"
    )
    partial = output.with_name(f".{output.stem}.part.mp4")
    command = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(clean_path),
        "-stream_loop",
        "-1",
        "-i",
        str(music),
        *brand_inputs,
        "-filter_complex",
        filtergraph,
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        *_encode_args({"crf": 21}),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k" if branded_role == "branded_whole" else "128k",
        "-t",
        f"{duration_s:.3f}",
        "-movflags",
        "+faststart",
        str(partial),
    ]
    try:
        _run_encode(
            command,
            stage=branded_role,
            content_item_id=clean_asset.content_item_id,
            media_duration_s=duration_s,
            cwd=output.parent,
        )
    except subprocess.CalledProcessError as exc:
        partial.unlink(missing_ok=True)
        raise RenderError(
            f"brand derivation of {clean_asset.content_item_id} for "
            f"{profile.brand_id}: {exc.stderr.strip()[-800:]}"
        ) from exc

    partial.replace(output)
    combined_warnings = [*(warnings or []), *brand_warnings]
    return manifests.inspect_expected_asset(
        clean_asset.video_id,
        clean_asset.render_revision,
        branded_role,
        clean_asset.content_item_id,
        expected_duration_s=duration_s,
        brand_id=profile.brand_id,
        lineage_asset_id=clean_asset.asset_id,
        warnings=combined_warnings,
    )


def segments_within(segments: list[dict], start_s: float, end_s: float) -> list[dict]:
    """The segments this chunk must burn subtitles for.

    Both comparisons are strict, which is the whole point. F4 cuts chunks on
    Whisper segment edges, so a segment that ends exactly where the next chunk
    begins belongs to the earlier chunk alone. A `>=` here would put it in both
    and the joined video would show the same line twice.

    A segment that genuinely straddles a boundary is still returned to both
    chunks. That is deliberate — half a subtitle is worse than a repeated one —
    and it stays unreachable only while F4 keeps its guarantee, which is what
    `test_chunk_boundaries_fall_on_segment_edges_and_respect_the_ceiling`
    pins in the transcript service.
    """
    return [
        segment for segment in segments
        if float(segment["end"]) > start_s and float(segment["start"]) < end_s
    ]


def concat(video_id: str, clips: list[Path]) -> Path:
    """Join the rendered chunks into the whole-video output.

    Stream copy, not a re-encode: every clip came out of the same encoder with
    the same settings, so there is nothing to reconcile and re-encoding would
    cost a second full pass to lose a generation of quality.
    """
    directory = library.processed_dir(video_id)
    directory.mkdir(parents=True, exist_ok=True)
    listing = directory / "clips.txt"
    listing.write_text(
        "".join(f"file '{clip.resolve().as_posix()}'\n" for clip in clips), encoding="utf-8"
    )

    output = directory / CONCAT_NAME
    partial = directory / (CONCAT_NAME + ".part.mp4")
    command = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(listing),
        "-c", "copy", "-movflags", "+faststart", str(partial),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        partial.unlink(missing_ok=True)
        raise RenderError(f"joining {video_id}: {exc.stderr.strip()[-800:]}") from exc

    partial.replace(output)
    return output


def drop_surplus_chunk_dirs(video_id: str, keep: int) -> None:
    """Remove chunk folders past the end of the current chunk list.

    Re-chunking at a coarser threshold leaves folders for indexes that no
    longer exist, and the join would happily include one. Only the surplus is
    removed: wiping the whole tree first would throw away a previous render
    before knowing whether this one succeeds, which is the wrong trade when a
    chunk is minutes of ffmpeg. Every surviving chunk is overwritten in place
    through a partial name anyway.
    """
    root = library.video_dir(video_id) / "chunks"
    if not root.is_dir():
        return
    for directory in root.iterdir():
        if directory.is_dir() and directory.name.isdigit() and int(directory.name) >= keep:
            shutil.rmtree(directory, ignore_errors=True)
