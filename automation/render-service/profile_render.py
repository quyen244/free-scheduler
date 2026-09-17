"""Measure where one real brand render actually spends its time.

The render is a single ffmpeg process, so "it is slow" carries no information
about which layer costs what. This drives the real `compose_clean` graph for a
real published brand over the real source, then re-runs it with one layer group
removed at a time. The delta between a run and the full graph is that group's
cost, measured rather than reasoned about.

    docker compose exec render-service python profile_render.py an-so vertical 20
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import brands
import library
import preset as presets
import render
import subs


def _strip(config: dict, drop: set[str]) -> dict:
    """The same layout with one group of layers taken out."""
    out = copy.deepcopy(config)
    if "images" in drop:
        out["images"] = []
    if "background" in drop:
        out["background"] = ""
    if "blur" in drop:
        out["blur_layers"] = []
        out["blur_regions"] = []
    if "host" in drop:
        out["host"] = None
    if "text" in drop:
        out["text_layers"] = []
        out["caption_top"] = None
        out["caption_bottom"] = None
    if "subtitle" in drop:
        out["subtitle"] = None
    if "video" in drop:
        out["video_visible"] = False
    return out


def _run(command: list[str], cwd: Path) -> float:
    started = time.monotonic()
    result = subprocess.run(command, capture_output=True, text=True, cwd=cwd)
    elapsed = time.monotonic() - started
    if result.returncode != 0:
        raise SystemExit(f"ffmpeg failed: {result.stderr.strip()[-1500:]}")
    return elapsed


def main(brand_id: str, aspect: str, duration_s: float, start_s: float) -> int:
    video_id = sys.argv[5] if len(sys.argv) > 5 else "hS3VXBeEv0I"
    revision = brands.latest_revision(brand_id)
    config = brands.render_config(brands.load_published(brand_id, revision), aspect)

    raw = library.raw_path(video_id)
    voice = library.load_voice_track(video_id)
    source = render.probe(raw)
    segments = json.loads(
        (Path("/data") / video_id / "transcript.vi.json").read_text(encoding="utf-8")
    )
    segments = segments.get("segments", segments) if isinstance(segments, dict) else segments

    work = Path(tempfile.mkdtemp(prefix=f"profile-{brand_id}-"))
    subtitle_name = "profile.subs.ass"
    within = render.segments_within(segments, start_s, start_s + duration_s)
    if config.get("subtitle"):
        subs.write(
            work / subtitle_name,
            subs.build(within, config["subtitle"], config["canvas"]["w"],
                       config["canvas"]["h"], start_s),
        )

    cases: list[tuple[str, set[str]]] = [
        ("full", set()),
        ("no_subtitle", {"subtitle"}),
        ("no_text", {"text"}),
        ("no_host", {"host"}),
        ("no_blur", {"blur"}),
        ("no_images", {"images"}),
        ("video_only", {"subtitle", "text", "host", "blur", "images"}),
        ("canvas_only", {"subtitle", "text", "host", "blur", "images", "video"}),
    ]

    span = ["-ss", f"{start_s:.3f}", "-t", f"{duration_s:.3f}"]
    print(f"source={source.width}x{source.height} fps={source.frame_rate} "
          f"aspect={aspect} duration_s={duration_s} encoder={render.encoder()}")
    print(f"{'case':<14} {'elapsed_s':>10} {'rtf':>7} {'delta_s':>9} {'share':>7}")

    results: dict[str, float] = {}
    for name, drop in cases:
        variant = _strip(config, drop)
        geometry = presets.resolve(variant, source.width, source.height)
        graph, extra_inputs, _ = render.compose_clean(
            variant, geometry, source, work, f"profile-{name}", subtitle_name,
            duration_s, fields={"title": "Tieu de kiem tra", "part": "1"},
        )
        command = [
            "ffmpeg", "-y", "-loglevel", "error",
            *span, "-i", str(raw),
            *render._background_args(variant, geometry, duration_s, source.frame_rate),
            *span, "-i", str(voice),
            *extra_inputs,
            "-filter_complex", f"{graph};[{render._VOICE_INPUT}:a]apad[aout]",
            "-map", "[vout]", "-map", "[aout]",
            *render._encode_args({}), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-t", f"{duration_s:.3f}",
            str(work / f"{name}.mp4"),
        ]
        elapsed = _run(command, work)
        results[name] = elapsed
        base = results["full"]
        delta = base - elapsed
        print(f"{name:<14} {elapsed:>10.2f} {duration_s / elapsed:>7.3f} "
              f"{delta:>9.2f} {(delta / base * 100 if base else 0):>6.1f}%")

    print(f"\nwork dir: {work}")
    return 0


if __name__ == "__main__":
    raise SystemExit(
        main(
            sys.argv[1],
            sys.argv[2] if len(sys.argv) > 2 else "vertical",
            float(sys.argv[3]) if len(sys.argv) > 3 else 20.0,
            float(sys.argv[4]) if len(sys.argv) > 4 else 60.0,
        )
    )
