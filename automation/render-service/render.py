"""The ffmpeg half: blur, composite, logo, watermark, burned subtitles.

One filtergraph per chunk, built from the preset's geometry. Filenames inside
a filtergraph are read by ffmpeg's own parser, where `:` separates options and
`\\` escapes — a Windows-shaped or space-carrying path turns into a syntax
error rather than a missing file. So every command here runs with its working
directory set to the chunk folder and names its files relatively.
"""

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import library
import manifest as manifests
import preset as presets
import brand as brands
import subs
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


def probe(path: Path) -> Source:
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
    stream = data["streams"][0]
    return Source(
        width=int(stream["width"]),
        height=int(stream["height"]),
        duration_s=float(data["format"]["duration"]),
    )


@lru_cache(maxsize=1)
def encoder() -> str:
    """Pick the h264 encoder once, by trying it rather than by asking.

    `ffmpeg -encoders` lists h264_nvenc whenever the binary was built with it,
    which says nothing about whether this container can reach a GPU. A
    one-frame encode is the only answer that is not a guess.
    """
    probe_command = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=black:s=64x64:d=0.1",
        "-c:v", "h264_nvenc", "-f", "null", "-",
    ]
    try:
        subprocess.run(probe_command, check=True, capture_output=True, text=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.info("h264_nvenc is not usable here; encoding on the CPU with libx264")
        return "libx264"
    logger.info("encoding with h264_nvenc")
    return "h264_nvenc"


def _encode_args(config: dict) -> list[str]:
    chosen = encoder()
    if chosen == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", str(config.get("crf", 21))]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(config.get("crf", 21))]


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

    partial = output.with_name("whole-16x9.part.mp4")
    filtergraph = (
        "[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,"
        "setsar=1,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"ass={subtitle_name}[vout];[1:a]apad[aout]"
    )
    command = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(raw),
        "-i",
        str(voice),
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
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
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
        warnings=warnings,
    )


def render_clean_vertical(
    video_id: str,
    render_revision: int,
    chunk: dict,
    preset: dict,
    segments: list[dict],
    texts: dict[str, str] | None = None,
    warnings: list[str] | None = None,
) -> manifests.MediaAsset:
    """Render one brand-neutral, revisioned 1080x1920 chunk master."""
    canvas = preset.get("canvas") or {}
    if (int(canvas.get("w", 0)), int(canvas.get("h", 0))) != (1080, 1920):
        raise RenderError("clean vertical preset must use a 1080x1920 canvas")
    if preset.get("logo") or (preset.get("watermark") or {}).get("text"):
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

    steps, video_label = _blur_chain(geometry.blur_regions, source)
    box = geometry.video
    steps.extend(
        [
            f"[{video_label}]scale={box.w}:{box.h},setsar=1[vid]",
            f"[1:v]scale={geometry.canvas_w}:{geometry.canvas_h},setsar=1[bg]",
            f"[bg][vid]overlay={box.x}:{box.y}[comp]",
        ]
    )
    current = "comp"
    family = str((preset.get("subtitle") or {}).get("font", "DejaVu Sans"))
    for key in ("caption_top", "caption_bottom"):
        value = str((texts or {}).get(key, "")).strip()
        config = preset.get(key)
        if not config or not value:
            continue
        textfile = _write_text(output.parent, f"{content_item_id}.{key}.txt", value)
        nxt = f"{key}_out"
        steps.append(f"[{current}]{_drawtext(config, textfile, geometry, family)}[{nxt}]")
        current = nxt
    steps.append(f"[{current}]ass={subtitle_name}[vout]")
    steps.append("[2:a]apad[aout]")

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
        "-loop",
        "1",
        "-t",
        f"{duration_s:.3f}",
        "-i",
        str(library.background_path(str(preset.get("background", "")))),
        "-ss",
        f"{start_s:.3f}",
        "-t",
        f"{duration_s:.3f}",
        "-i",
        str(voice),
        "-filter_complex",
        ";".join(steps),
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
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
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
        warnings=warnings,
    )


def render_branded_variant(
    clean_asset: manifests.MediaAsset,
    profile: brands.MockBrandProfile,
    warnings: list[str] | None = None,
) -> manifests.MediaAsset:
    """Derive one brand-specific video from a validated clean master.

    The mock music settings are deliberately a quiet, looped bed with short
    fades. Speech-aware ducking is not enabled until its thresholds have been
    calibrated against representative narration fixtures.
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
    watermark = profile.watermark
    min_dimension = min(clean_asset.probe.width, clean_asset.probe.height)
    margin = max(int(round(min_dimension * watermark.margin_ratio)), 2)
    font_size = max(int(round(min_dimension * watermark.font_size_ratio)), 12)
    x = str(margin) if watermark.anchor.endswith("left") else f"w-text_w-{margin}"
    y = str(margin) if watermark.anchor.startswith("top") else f"h-text_h-{margin}"
    watermark_file = _write_text(
        output.parent,
        f"{clean_asset.content_item_id}.watermark.txt",
        watermark.text,
    )

    fade_in = min(profile.signature_music.fade_in_s, duration_s / 2)
    fade_out = min(profile.signature_music.fade_out_s, duration_s / 2)
    fade_out_start = max(duration_s - fade_out, 0)
    colour = watermark.color.lstrip("#")
    font = presets.font_file("DejaVu Sans")
    filtergraph = (
        f"[0:v]drawtext=fontfile={font}:textfile={watermark_file}:"
        f"fontsize={font_size}:fontcolor=0x{colour}@{watermark.opacity}:"
        f"borderw={max(font_size // 18, 1)}:bordercolor=0x000000@{watermark.opacity}:"
        f"x={x}:y={y}[vout];"
        "[0:a]aresample=48000,asetpts=PTS-STARTPTS[speech];"
        f"[1:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
        f"volume={profile.signature_music.volume_db}dB,atrim=0:{duration_s:.3f},"
        f"afade=t=in:st=0:d={fade_in:.3f},"
        f"afade=t=out:st={fade_out_start:.3f}:d={fade_out:.3f},"
        "asetpts=PTS-STARTPTS[music];"
        "[speech][music]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
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
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            cwd=output.parent,
        )
    except subprocess.CalledProcessError as exc:
        partial.unlink(missing_ok=True)
        raise RenderError(
            f"brand derivation of {clean_asset.content_item_id} for "
            f"{profile.brand_id}: {exc.stderr.strip()[-800:]}"
        ) from exc

    partial.replace(output)
    combined_warnings = [
        "mock signature-music mix uses provisional -30 dB-style configuration; "
        "speech ducking is pending fixture calibration",
        *(warnings or []),
    ]
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
