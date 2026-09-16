"""Drive the brand-owned render loop end to end against a synthetic source.

There is no ingested video in this checkout, so this builds one: a colour-bar
`raw.mp4`, a voice track, a transcript and two chunk rows. Then it runs the
real `variants.render_brand_revision`, which encodes with ffmpeg, probes every
output and validates a `brand_owned` manifest. A frame of each asset is kept
for a human to look at.

    docker compose exec render-service python check_brand_revision.py
    docker compose exec render-service python check_brand_revision.py --keep
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import library
import manifest as manifests
import variants
from shared import pipeline_db

VIDEO_ID = "brandLoopAA"
DURATION_S = 24.0
CHUNKS = [(0.0, 12.0), (12.0, 24.0)]
BRAND_REVISIONS = {"an-so": 1, "mock-brand": 1}
OUT = Path("/data/artifacts")


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


def _build_source() -> None:
    directory = library.video_dir(VIDEO_ID)
    directory.mkdir(parents=True, exist_ok=True)
    _ffmpeg(
        "-f", "lavfi", "-t", f"{DURATION_S}", "-i", "smptebars=s=1920x1080:r=30",
        "-f", "lavfi", "-t", f"{DURATION_S}", "-i", "sine=frequency=220:r=48000",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        str(library.raw_path(VIDEO_ID)),
    )
    # Speech stand-in: loud enough that the music sidechain has something to
    # duck against, so a silent bed cannot make the mix look correct.
    _ffmpeg(
        "-f", "lavfi", "-t", f"{DURATION_S}",
        "-i", "sine=frequency=440:r=48000:sample_rate=48000",
        "-ac", "2", str(library.voice_path(VIDEO_ID)),
    )
    (directory / "transcript.json").write_text(
        json.dumps(
            {
                "segments": [
                    {"start": start, "end": start + 4.0, "text": f"Cau noi thu {index + 1}"}
                    for index, (start, _) in enumerate(CHUNKS)
                ]
            }
        ),
        encoding="utf-8",
    )
    pipeline_db.init()
    pipeline_db.record_ingested(
        VIDEO_ID, "https://example.invalid/brand-loop", "Kiem tra vong lap brand",
        DURATION_S, width=1920, height=1080,
    )
    pipeline_db.replace_chunks(
        VIDEO_ID,
        [
            {
                "idx": index, "name": f"part_{index + 1}",
                "start_s": start, "end_s": end, "duration_s": end - start,
                "boundary_shift_s": 0.0, "text": f"Noi dung phan {index + 1}",
            }
            for index, (start, end) in enumerate(CHUNKS)
        ],
    )


def _keep_frame(asset: manifests.MediaAsset) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    frame = OUT / f"loop-{asset.brand_id}-{asset.content_item_id}.png"
    _ffmpeg("-ss", "1", "-i", asset.path, "-frames:v", "1", str(frame))
    return frame


def _cleanup() -> None:
    shutil.rmtree(library.video_dir(VIDEO_ID), ignore_errors=True)
    with pipeline_db.connect() as conn:
        conn.execute("DELETE FROM chunks WHERE video_id = ?", (VIDEO_ID,))
        conn.execute("DELETE FROM videos WHERE video_id = ?", (VIDEO_ID,))


def main(keep: bool) -> int:
    _cleanup()
    _build_source()
    try:
        result = variants.render_brand_revision(
            VIDEO_ID, 1, brand_revisions=BRAND_REVISIONS, metadata_revision_id=None
        )
        print(f"state={result.state} topology={result.topology} "
              f"brands={result.brand_revisions} assets={len(result.assets)}")
        for failure in result.failures:
            print(f"  FAILURE {failure.code}: {failure.message}", file=sys.stderr)
        for asset in sorted(result.assets, key=lambda a: (a.brand_id or "", a.content_item_id)):
            frame = _keep_frame(asset)
            print(
                f"  {asset.brand_id}/{asset.content_item_id} {asset.role} "
                f"{asset.probe.width}x{asset.probe.height} "
                f"{asset.probe.duration_s:.3f}s {asset.probe.video_codec}/"
                f"{asset.probe.audio_codec} lineage={asset.lineage_asset_id} "
                f"bytes={asset.bytes} -> {frame.name}"
            )
        expected = len(BRAND_REVISIONS) * (1 + len(CHUNKS))
        print(f"expected={expected} got={len(result.assets)}")
        return 0 if result.state == "ready" and len(result.assets) == expected else 1
    finally:
        if not keep:
            _cleanup()


if __name__ == "__main__":
    raise SystemExit(main("--keep" in sys.argv))
