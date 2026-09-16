"""Render one published brand layout over a synthetic source and keep a frame.

A brand config that validates is not a brand config that composites: the
filtergraph is where a z-order, a fit mode or an alpha mask actually has to
work. This drives `compose_clean` with a colour-bar source so the check needs
no ingested video, then writes a PNG for a human to look at.

    docker compose exec render-service python check_brand_render.py an-so vertical
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import brands
import preset as presets
import render

DURATION_S = 3.0
OUT = Path("/data/artifacts")


def main(brand_id: str, aspect: str) -> int:
    revision = brands.latest_revision(brand_id)
    if revision is None:
        print(f"{brand_id} has no published revision", file=sys.stderr)
        return 1
    config = brands.render_config(brands.load_published(brand_id, revision), aspect)

    canvas = config["canvas"]
    # A 16:9 colour-bar source, which is what the real footage is, so the
    # contain-fit inside the video rectangle is exercised rather than skipped.
    source = render.Source(width=1920, height=1080, duration_s=DURATION_S)

    work = Path(tempfile.mkdtemp(prefix=f"{brand_id}-{aspect}-"))
    geometry = presets.resolve(config, source.width, source.height)
    subtitle_name = "check.subs.ass"
    subtitle = config.get("subtitle") or {}
    (work / subtitle_name).write_text(
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {canvas['w']}\nPlayResY: {canvas['h']}\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, "
        "Outline, Alignment, MarginV\n"
        f"Style: Default,DejaVu Sans,{subtitle.get('size', 46)},"
        "&H00FFFFFF,3,2,80\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Text\n"
        "Dialogue: 0,0:00:00.00,0:00:03.00,Default,Phu de mau kiem tra\n",
        encoding="utf-8",
    )

    graph, extra_inputs, warnings = render.compose_clean(
        config,
        geometry,
        source,
        work,
        "check",
        subtitle_name,
        DURATION_S,
        fields={"title": "Tieu de kiem tra", "part": "1"},
    )

    OUT.mkdir(parents=True, exist_ok=True)
    frame = OUT / f"brand-{brand_id}-{aspect}.png"
    command = [
        "ffmpeg", "-y", "-loglevel", "error",
        # 0: the source footage stand-in.
        "-f", "lavfi", "-t", f"{DURATION_S}", "-i", f"smptebars=s=1920x1080:r=30",
        # 1: the black canvas every layout is drawn onto.
        "-f", "lavfi", "-t", f"{DURATION_S}",
        "-i", f"color=c=black:s={canvas['w']}x{canvas['h']}:r=30",
        # 2: the voice track, unused by the video graph but a fixed input slot.
        "-f", "lavfi", "-t", f"{DURATION_S}", "-i", "anullsrc=r=48000:cl=stereo",
        *extra_inputs,
        "-filter_complex", graph,
        "-map", "[vout]",
        "-frames:v", "1",
        str(frame),
    ]
    result = subprocess.run(command, capture_output=True, text=True, cwd=work)
    if result.returncode != 0:
        print(graph, file=sys.stderr)
        print(result.stderr.strip()[-2000:], file=sys.stderr)
        return 1

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(frame)],
        capture_output=True, text=True, check=True,
    )
    print(f"{brand_id}/{aspect}: {frame} {probe.stdout.strip()} "
          f"inputs={extra_inputs.count('-i')} warnings={warnings}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "vertical"))
