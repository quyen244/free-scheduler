"""The whole YouTube asset is a direct 16:9 render, never vertical concat."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import library
import render
from errors import RenderError


pytestmark = pytest.mark.no_pipeline
VIDEO_ID = "landscapeAA"


def _build_inputs(root: Path) -> None:
    directory = root / VIDEO_ID
    directory.mkdir(parents=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1280x720:rate=25:duration=0.5",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(directory / "raw.mp4"),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100:duration=0.5",
            "-c:a",
            "pcm_s16le",
            str(directory / "voice.wav"),
        ],
        check=True,
        capture_output=True,
    )


def _landscape_preset() -> dict:
    return json.loads(
        Path("/data/presets/yt-landscape.json").read_text(encoding="utf-8")
    )


def test_whole_asset_is_direct_landscape_with_voice_and_subtitles(tmp_path, monkeypatch):
    preset = _landscape_preset()
    _build_inputs(tmp_path)
    monkeypatch.setattr(library, "settings", SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(render, "encoder", lambda: "libx264")

    asset = render.render_clean_whole(
        VIDEO_ID,
        1,
        preset,
        [{"start": 0.0, "end": 0.5, "text": "Một phụ đề tiếng Việt."}],
    )

    expected = (
        tmp_path
        / VIDEO_ID
        / "outputs/clean/revision/1/whole-16x9.mp4"
    )
    assert Path(asset.path) == expected
    assert asset.role == "clean_whole"
    assert (asset.probe.width, asset.probe.height) == (1920, 1080)
    assert asset.probe.video_codec == "h264"
    assert asset.probe.audio_codec == "aac"
    assert asset.probe.duration_s == pytest.approx(0.5, abs=0.1)
    assert "Một phụ đề tiếng Việt." in expected.with_name(
        "whole-16x9.subs.ass"
    ).read_text(encoding="utf-8")


def test_whole_asset_refuses_a_vertical_preset(tmp_path, monkeypatch):
    _build_inputs(tmp_path)
    monkeypatch.setattr(library, "settings", SimpleNamespace(data_dir=tmp_path))

    with pytest.raises(RenderError, match="1920x1080"):
        render.render_clean_whole(
            VIDEO_ID,
            1,
            {"canvas": {"w": 1080, "h": 1920}},
            [],
        )
