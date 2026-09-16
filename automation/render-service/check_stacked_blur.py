"""Prove a blur layer's `z` changes what it hides, with real ffmpeg frames.

A string test shows the filter is in the graph in the right order. It cannot
show that ffmpeg accepts a three-step layer in the middle of the chain, nor
that the rectangle lands on the pixels the operator dragged it over. This
renders the same layout twice - one blur under the logo, one over it - onto a
colour-bar source, so the difference is something a human can look at.

    docker compose exec render-service python check_stacked_blur.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import preset as presets
import render

DURATION_S = 1.0
OUT = Path("/data/artifacts")
CANVAS = {"w": 1080, "h": 1920}
# Over the footage, wherever the footage happens to land: source coordinates.
BLUR = {"id": "plate", "x": 0.30, "y": 0.35, "w": 0.40, "h": 0.30}


def _config(blur_z: int) -> dict:
    return {
        "canvas": CANVAS,
        "video_rect": {"x": 0.0, "y": 0.28, "w": 1.0, "h": 0.44},
        "video_z": 10,
        "video_visible": True,
        "blur_regions": [],
        "blur_layers": [{**BLUR, "z": blur_z}],
        "images": [
            {
                "id": "logo",
                "path": "logo.png",
                # Deliberately inside the blur rectangle: under the blur it
                # disappears, over it stays sharp.
                "x": 0.36, "y": 0.44, "w": 0.28, "h": 0.08,
                "opacity": 1.0, "fit": "fill", "z": 30,
            }
        ],
        "host": None,
        "text_layers": [],
        "subtitle": None,
    }


def _render(work: Path, blur_z: int, name: str) -> int:
    config = _config(blur_z)
    source = render.Source(width=1920, height=1080, duration_s=DURATION_S)
    geometry = presets.resolve(config, source.width, source.height)
    graph, extra_inputs, warnings = render.compose_clean(
        config, geometry, source, work, "check", "check.ass", DURATION_S
    )

    frame = OUT / name
    command = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-t", f"{DURATION_S}", "-i", "smptebars=s=1920x1080:r=30",
        "-f", "lavfi", "-t", f"{DURATION_S}",
        "-i", f"color=c=black:s={CANVAS['w']}x{CANVAS['h']}:r=30",
        "-f", "lavfi", "-t", f"{DURATION_S}", "-i", "anullsrc=r=48000:cl=stereo",
        *extra_inputs,
        "-filter_complex", graph,
        "-map", "[vout]", "-frames:v", "1", str(frame),
    ]
    result = subprocess.run(command, capture_output=True, text=True, cwd=work)
    if result.returncode != 0:
        print(graph, file=sys.stderr)
        print(result.stderr.strip()[-2000:], file=sys.stderr)
        return 1
    print(f"blur z={blur_z}: {frame} warnings={warnings}")
    return 0


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="stacked-blur-"))
    # A flat magenta bar: any blur over it is obvious, and any blur that missed
    # it leaves it exactly as flat.
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "color=c=magenta:s=302x152", "-frames:v", "1", "logo.png"],
        check=True, cwd=work,
    )
    OUT.mkdir(parents=True, exist_ok=True)
    return (
        _render(work, 11, "blur-z11-under-the-logo.png")
        or _render(work, 99, "blur-z99-over-the-logo.png")
    )


if __name__ == "__main__":
    raise SystemExit(main())
