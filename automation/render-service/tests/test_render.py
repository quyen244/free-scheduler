"""Rendering, against the real running server and real ffmpeg.

The claim these exist for is the awkward one: "the same preset lands correctly
on both a 720p and a 1080p source". Preset arithmetic is checked in
test_preset.py; what is checked here is the finished frame, because a
filtergraph can be given correct numbers and still put the picture in the
wrong place.
"""

import shutil
import subprocess
from pathlib import Path

import httpx
import numpy as np
import pytest
from conftest import SERVICE, SHORT_VIDEO_ID, await_callback, await_job, callback_recorder, resume_url

import library
from shared import pipeline_db

DATA = Path("/data")
PRESET = "bi-mat-bi-an"

# Two synthetic sources of the same content at different resolutions. Ids are
# the shape a YouTube id has, because everything downstream validates that.
HD_ID = "tst720pAAAA"
FHD_ID = "tst1080pAAA"


def _build_source(video_id: str, width: int, height: int) -> None:
    """A stand-in video at a chosen resolution, sharing the real one's audio.

    Rescaled from the real source rather than generated: a test pattern would
    prove the geometry and nothing about how a real frame survives the chain.
    """
    directory = DATA / video_id
    directory.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(DATA / SHORT_VIDEO_ID / "raw.mp4"),
            "-vf", f"scale={width}:{height}", "-an", str(directory / "raw.mp4"),
        ],
        check=True, capture_output=True,
    )
    for name in ("transcript.json", "transcript.vi.json", "voice.wav", "voice.json"):
        source = DATA / SHORT_VIDEO_ID / name
        if source.is_file():
            shutil.copy(source, directory / name)

    transcript = library.load_transcript(video_id)
    pipeline_db.record_ingested(video_id, f"test://{video_id}", video_id, 19.0)
    pipeline_db.replace_chunks(
        video_id,
        [{"idx": 0, "start_s": 0.0, "end_s": 19.0, "duration_s": 19.0,
          "text": transcript["transcript"]}],
    )


def _first_frame(path: Path, at_s: float = 6.0) -> np.ndarray:
    """One frame as an 8-bit greyscale array, straight out of ffmpeg."""
    result = subprocess.run(
        [
            "ffmpeg", "-loglevel", "error", "-ss", str(at_s), "-i", str(path),
            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-",
        ],
        check=True, capture_output=True,
    )
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True,
    )
    width, height = (int(value) for value in probe.stdout.strip().split(","))
    return np.frombuffer(result.stdout, dtype=np.uint8).reshape(height, width)


def _video_band(frame: np.ndarray) -> tuple[int, int]:
    """The rows the source video occupies, found by brightness.

    The background is a near-black starfield and the composited source is lit,
    so the band stands out by an order of magnitude. This is the measurement
    the resolution-independence claim rests on.
    """
    rows = frame.mean(axis=1)
    lit = np.flatnonzero(rows > rows.max() * 0.35)
    return int(lit[0]), int(lit[-1])


def _render(video_id: str, **extra: object) -> dict:
    response = httpx.post(
        f"{SERVICE}/render/jobs", json={"video_id": video_id, **extra}, timeout=30
    )
    assert response.status_code == 202, response.text
    return await_job(response.json()["job_id"], timeout=900)


@pytest.fixture(scope="module")
def rendered() -> dict:
    return _render(SHORT_VIDEO_ID)


@pytest.fixture(scope="module")
def two_resolutions():
    _build_source(HD_ID, 1280, 720)
    _build_source(FHD_ID, 1920, 1080)
    try:
        yield {
            720: _render(HD_ID),
            1080: _render(FHD_ID),
        }
    finally:
        for video_id in (HD_ID, FHD_ID):
            shutil.rmtree(DATA / video_id, ignore_errors=True)
            with pipeline_db.connect() as conn:
                conn.execute("DELETE FROM chunks WHERE video_id = ?", (video_id,))
                conn.execute("DELETE FROM videos WHERE video_id = ?", (video_id,))


def test_the_job_is_accepted_long_before_the_work_is_done():
    with callback_recorder() as recorder:
        response = httpx.post(
            f"{SERVICE}/render/jobs",
            json={"video_id": SHORT_VIDEO_ID, "callback_url": resume_url(recorder)},
            timeout=30,
        )
        assert response.status_code == 202
        assert response.elapsed.total_seconds() < 5.0

        delivered = await_callback(recorder, timeout=900)
        assert delivered["state"] == "done"
        assert delivered["result"]["processed_path"].endswith("final.mp4")


def test_the_output_is_the_canvas_the_preset_asked_for(rendered):
    assert rendered["state"] == "done", rendered["error"]
    chunk = rendered["result"]["chunks"][0]
    assert (chunk["width"], chunk["height"]) == (1080, 1920)


def test_the_output_is_as_long_as_the_chunk(rendered):
    assert rendered["result"]["chunks"][0]["duration_s"] == pytest.approx(19.0, abs=0.5)


def _stream_duration(path: Path, kind: str) -> float:
    """One stream's own length, not the container's rounded-up header."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", kind,
         "-show_entries", "stream=duration", "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True,
    )
    value = result.stdout.strip().splitlines()
    if not value or not value[0] or value[0] == "N/A":
        raise AssertionError(f"{path} has no {kind} stream duration to read")
    return float(value[0])


def test_the_padded_voice_stops_at_the_end_of_the_chunk(rendered):
    """apad fills a chunk whose voice ends early. It must not overrun it.

    An unbounded audio filter needs an explicit end. Given only -shortest,
    ffmpeg 6.1 both overruns the span and fails the command, so the length is
    set on the output instead. Audio and video must land within one frame of
    the span the chunk was cut from.
    """
    assert rendered["state"] == "done", rendered["error"]
    chunk = rendered["result"]["chunks"][0]
    clip = Path(chunk["final_path"])

    with pipeline_db.connect() as conn:
        row = conn.execute(
            "SELECT start_s, end_s FROM chunks WHERE video_id = ? AND idx = ?",
            (SHORT_VIDEO_ID, chunk["idx"]),
        ).fetchone()
    assert row is not None, "the chunk row the clip was cut from is missing"

    span = float(row["end_s"]) - float(row["start_s"])
    frame = 1.0 / 25.0

    video = _stream_duration(clip, "v:0")
    audio = _stream_duration(clip, "a:0")

    assert video == pytest.approx(span, abs=frame), f"video {video} against span {span}"
    assert audio == pytest.approx(span, abs=frame), f"audio {audio} against span {span}"
    assert abs(audio - video) < frame, f"audio {audio} and video {video} disagree"


def test_the_output_carries_the_vietnamese_voice_and_not_the_original(rendered):
    # The point of the pipeline. A render that kept the source audio looks
    # perfect in a still frame and is wrong the moment anyone plays it.
    path = Path(rendered["result"]["chunks"][0]["final_path"])
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_name,channels", "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True,
    )
    assert result.stdout.strip() == "aac,1"


def test_the_chunk_row_records_where_the_file_landed(rendered):
    chunk = next(c for c in pipeline_db.chunks_for(SHORT_VIDEO_ID) if c["idx"] == 0)
    assert chunk["status"] == "rendered"
    assert chunk["final_path"] == rendered["result"]["chunks"][0]["final_path"]


def test_the_video_moves_to_the_rendered_stage(rendered):
    with pipeline_db.connect() as conn:
        row = conn.execute(
            "SELECT stage, preset FROM videos WHERE video_id = ?", (SHORT_VIDEO_ID,)
        ).fetchone()
    assert row["stage"] in ("rendered", "captioned", "ready")
    assert row["preset"] == PRESET


def test_subtitles_are_burned_in_not_shipped_as_a_side_file(rendered):
    # A separate .srt is a file nobody uploads. The .ass is kept for diagnosis,
    # but the words have to be in the pixels.
    directory = library.chunk_dir(SHORT_VIDEO_ID, 0)
    assert (directory / "subs.ass").is_file()
    frame = _first_frame(directory / "final.mp4", at_s=6.0)
    # Below the video's bottom edge and above the watermark, so the only bright
    # thing that belongs here is text.
    band = frame[int(frame.shape[0] * 0.72) : int(frame.shape[0] * 0.77)]
    # Counted, not maxed: a single bright pixel is a star in the background,
    # while thousands of them are white letters with an outline.
    assert int((band > 200).sum()) > 1000


def test_the_same_preset_puts_the_picture_in_the_same_place_at_both_resolutions(two_resolutions):
    hd = _video_band(_first_frame(Path(two_resolutions[720]["result"]["chunks"][0]["final_path"])))
    fhd = _video_band(_first_frame(Path(two_resolutions[1080]["result"]["chunks"][0]["final_path"])))

    # Both sources are 16:9, so the composite must be identical, not merely
    # proportional — the canvas does not change when the source does.
    assert abs(hd[0] - fhd[0]) <= 4
    assert abs(hd[1] - fhd[1]) <= 4


def test_both_resolutions_produce_the_same_canvas(two_resolutions):
    for outcome in two_resolutions.values():
        chunk = outcome["result"]["chunks"][0]
        assert (chunk["width"], chunk["height"]) == (1080, 1920)


def test_a_render_can_be_limited_to_one_chunk():
    # For trying a preset change against one clip rather than a whole video.
    outcome = _render(SHORT_VIDEO_ID, only_chunk=0)
    assert outcome["state"] == "done"
    assert outcome["result"]["total_chunks"] == 1


def test_a_video_with_no_voice_track_is_refused_before_a_job_is_created():
    # Rendering without one would produce a silent video that looks finished.
    response = httpx.post(
        f"{SERVICE}/render/jobs", json={"video_id": "aaaaaaaaaaa"}, timeout=30
    )
    assert response.status_code == 404


def test_an_unknown_preset_is_a_404_not_a_failed_job():
    response = httpx.post(
        f"{SERVICE}/render/jobs",
        json={"video_id": SHORT_VIDEO_ID, "preset": "no-such-preset"},
        timeout=30,
    )
    assert response.status_code == 404


def test_a_preset_name_cannot_escape_the_presets_directory():
    response = httpx.post(
        f"{SERVICE}/render/jobs",
        json={"video_id": SHORT_VIDEO_ID, "preset": "../../etc/passwd"},
        timeout=30,
    )
    assert response.status_code == 404
