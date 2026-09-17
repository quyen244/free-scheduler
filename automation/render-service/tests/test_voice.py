"""Voicing, against the real running server and the real model.

Needs the GPU: VieNeu v3 Turbo is a torch model and this exercises the real
graph. Run on a 19-second video to keep it to seconds. The arithmetic that decides where each segment lands is
tested exhaustively in test_timing.py without a model behind it; what is left
for here is whether the whole path produces a track that sits on the picture.
"""

import json
import wave
from pathlib import Path

import httpx
import numpy as np
import pytest
from conftest import SERVICE, SHORT_VIDEO_ID, await_callback, await_job, callback_recorder, resume_url

import timing

DATA = Path("/data")

# The criterion: a full voice track lines up with the original video within
# 50 ms per segment.
ALIGNMENT_TOLERANCE_MS = 50.0


@pytest.fixture(scope="module")
def voiced() -> dict:
    response = httpx.post(
        f"{SERVICE}/voice/jobs", json={"video_id": SHORT_VIDEO_ID}, timeout=30
    )
    assert response.status_code == 202
    return await_job(response.json()["job_id"], timeout=600)


def test_the_job_is_accepted_long_before_the_work_is_done():
    with callback_recorder() as recorder:
        response = httpx.post(
            f"{SERVICE}/voice/jobs",
            json={"video_id": SHORT_VIDEO_ID, "callback_url": resume_url(recorder)},
            timeout=30,
        )
        assert response.status_code == 202
        assert response.elapsed.total_seconds() < 5.0
        assert response.json()["state"] == "queued"

        # And the finished job arrives on its own, which is what an n8n Wait
        # node is waiting for.
        delivered = await_callback(recorder, timeout=600)
        assert delivered["state"] == "done"
        assert delivered["result"]["voice_path"].endswith("voice.wav")


def test_every_segment_starts_where_whisper_said_it_did(voiced):
    assert voiced["state"] == "done", voiced["error"]

    manifest = json.loads((DATA / SHORT_VIDEO_ID / "voice.json").read_text(encoding="utf-8"))
    with wave.open(str(DATA / SHORT_VIDEO_ID / "voice.wav"), "rb") as handle:
        rate = handle.getframerate()
        track = np.frombuffer(handle.readframes(handle.getnframes()), dtype=np.int16)

    floor = 0.01 * 32767
    for segment in manifest["segments"]:
        if segment["spoken_s"] == 0:
            continue
        at = int(round(segment["start"] * rate))
        window = np.abs(track[at : int(round(segment["end"] * rate))])
        loud = np.flatnonzero(window >= floor)
        assert loud.size, f"segment {segment['idx']} is silent"
        offset_ms = (loud[0] / rate) * 1000
        assert offset_ms <= ALIGNMENT_TOLERANCE_MS, (
            f"segment {segment['idx']} starts {offset_ms:.0f} ms late"
        )


def test_the_track_is_as_long_as_the_video_not_as_long_as_the_speech(voiced):
    # The failure this catches is concatenation: gaps dropped, every segment
    # after the first pulled earlier, and a track that ends minutes early.
    import library

    transcript = library.load_transcript(SHORT_VIDEO_ID)
    assert voiced["result"]["duration_s"] == pytest.approx(transcript["duration_s"], abs=0.1)


def test_a_short_segment_is_reported_rather_than_shipped_quietly(voiced):
    # This video's middle segment is a ten-second slot holding four seconds of
    # speech, because the English source paused. That is exactly the case the
    # warnings exist for.
    assert any("fills only" in warning for warning in voiced["warnings"])


def test_the_manifest_records_what_was_done_to_every_segment(voiced):
    manifest = json.loads((DATA / SHORT_VIDEO_ID / "voice.json").read_text(encoding="utf-8"))
    assert manifest["total_segments"] == len(manifest["segments"])
    for segment in manifest["segments"]:
        assert {"idx", "start", "end", "slot_s", "spoken_s", "ratio", "atempo"} <= segment.keys()
        # Recorded even when nothing was applied, so "1.0" means "measured and
        # needed nothing" rather than "never looked at".
        assert segment["atempo"] >= 1.0


def test_an_unknown_voice_is_refused_before_a_job_is_created():
    # The voice is validated against VieNeu's preset table, aliases included,
    # before any weights load. Failing now beats failing forty segments in.
    response = httpx.post(
        f"{SERVICE}/voice/jobs",
        json={"video_id": SHORT_VIDEO_ID, "voice": "my-own-voice"},
        timeout=30,
    )
    assert response.status_code == 400


def test_a_video_nobody_transcribed_is_a_404_not_a_failed_job():
    response = httpx.post(
        f"{SERVICE}/voice/jobs", json={"video_id": "aaaaaaaaaaa"}, timeout=30
    )
    assert response.status_code == 404


def test_a_video_id_that_is_not_one_is_refused():
    response = httpx.post(
        f"{SERVICE}/voice/jobs", json={"video_id": "../../etc/passwd"}, timeout=30
    )
    assert response.status_code == 400


def test_an_unknown_job_is_a_404():
    assert httpx.get(f"{SERVICE}/jobs/nosuchjob", timeout=10).status_code == 404


def test_the_track_is_written_whole_or_not_at_all(voiced):
    # Every write to the shared volume renames a partial into place. A
    # half-written voice track is not a parse error the way JSON is — it is a
    # shorter file that renders perfectly and goes quiet halfway through.
    assert not (DATA / SHORT_VIDEO_ID / "voice.wav.part").exists()
    assert not (DATA / SHORT_VIDEO_ID / "voice.json.part").exists()


def test_the_sample_rate_is_what_the_track_says_it_is(voiced):
    with wave.open(str(DATA / SHORT_VIDEO_ID / "voice.wav"), "rb") as handle:
        assert handle.getframerate() == timing.SAMPLE_RATE
        assert handle.getnchannels() == 1
