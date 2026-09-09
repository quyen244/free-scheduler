"""Joining chunks: what the concat step adds to the timeline, and what it must not.

The whole-video output is the sum of its chunks, so a per-chunk error is paid
once for every chunk. Measured on the reference run (11m23s, 3 chunks): the
joined file ends 120 ms long on video and 90 ms long on audio against the spans
it was cut from, a 30 ms A/V divergence - under one video frame, and under F6's
own 50 ms alignment tolerance. That is small enough to leave alone and large
enough to pin.

Nothing in the suite exercised `concat` with more than one chunk before this
file: the fixture video yields exactly one, so a change that turned 40 ms per
chunk into 400 ms would have shipped green.

**What is measured here.** These clips come from `lavfi`, so they have no
content and their durations are exact. That isolates the join. The separate
per-chunk overshoot - a real re-encode with `-ss`/`-t` landing one frame past
its span - belongs to `render_chunk`, and `test_the_output_is_as_long_as_the_chunk`
covers it against the real source.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import library
import render

# Nothing here talks to a service: ffmpeg, `render.concat` and a list of dicts.
# The pipeline preconditions cost minutes of ingest, transcribe, translate and
# voice, and this file needs none of them.
pytestmark = pytest.mark.no_pipeline

# The shape every id downstream validates, and not a real video.
CONCAT_ID = "tstconcatAA"

FPS = 25
FRAME_S = 1.0 / FPS
# AAC codes 1024 samples at a time, so the smallest amount of audio a container
# can gain or lose is one of those. At 48 kHz that is 21.3 ms, and it is the
# unit every number in this file lands on.
AAC_FRAME_S = 1024 / 48000

# Two clip counts over the same shape of content. Six against three is what
# turns "the error is small" into "the error does not accumulate", which is the
# claim that actually matters for a long video.
SPANS_THREE = (3.0, 4.0, 2.0)
SPANS_SIX = (3.0, 4.0, 2.0, 1.0, 5.0, 2.0)


def _measure(path: Path) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error",
         "-show_entries",
         "stream=codec_type,duration,start_time,nb_frames:format=duration",
         "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    )
    data = json.loads(result.stdout)
    out = {"format_s": float(data["format"]["duration"])}
    for stream in data["streams"]:
        kind = stream["codec_type"]
        out[f"{kind}_s"] = float(stream.get("duration", 0.0))
        out[f"{kind}_start_s"] = float(stream.get("start_time", 0.0))
        out[f"{kind}_frames"] = int(stream.get("nb_frames", 0))
    return out


def _build_clip(path: Path, seconds: float) -> None:
    """One chunk-shaped clip: the codecs and rate a real render writes.

    `concat -c copy` refuses streams it cannot join, so the clips have to match
    what `render_chunk` produces or this file would pass for the wrong reason.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc=size=320x180:rate={FPS}:duration={seconds}",
            "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={seconds}",
            "-t", f"{seconds:.3f}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-shortest", "-movflags", "+faststart", str(path),
        ],
        check=True, capture_output=True, text=True,
    )


def _join(spans: tuple[float, ...]) -> dict:
    directory = library.video_dir(CONCAT_ID)
    shutil.rmtree(directory, ignore_errors=True)
    try:
        clips = []
        for idx, seconds in enumerate(spans):
            clip = library.chunk_dir(CONCAT_ID, idx) / render.CLIP_NAME
            _build_clip(clip, seconds)
            clips.append(clip)
        joined = _measure(render.concat(CONCAT_ID, clips))
        return {
            "spans": spans,
            "requested_s": sum(spans),
            "clips": [_measure(clip) for clip in clips],
            "joined": joined,
        }
    finally:
        shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture(scope="module")
def three() -> dict:
    return _join(SPANS_THREE)


@pytest.fixture(scope="module")
def six() -> dict:
    return _join(SPANS_SIX)


class TestTheJoinConservesTheVideoTimeline:
    """Frames in, frames out. This is the claim `-c copy` is chosen for."""

    def test_the_joined_video_is_exactly_as_long_as_its_parts(self, three):
        expected = sum(clip["video_s"] for clip in three["clips"])
        assert three["joined"]["video_s"] == pytest.approx(expected, abs=0.005)

    def test_no_frame_is_dropped_or_duplicated_at_a_boundary(self, three):
        # A boundary that lost or repeated a frame would still play, and the
        # duration check above would miss a compensating pair. Counting catches
        # it.
        expected = sum(clip["video_frames"] for clip in three["clips"])
        assert three["joined"]["video_frames"] == expected
        assert three["joined"]["video_frames"] == int(round(three["requested_s"] * FPS))

    def test_every_chunk_reaches_the_output(self, six):
        assert six["joined"]["video_frames"] == sum(
            clip["video_frames"] for clip in six["clips"]
        )
        assert six["joined"]["format_s"] > max(clip["format_s"] for clip in six["clips"])


class TestTheJoinsOwnOverheadIsOneAudioFrame:
    """The join lengthens the audio by one AAC frame and starts the video late
    by the same amount - the encoder delay of the first clip, re-exposed when
    the container is rebuilt. Measured here at 21.3 ms, which is where the
    reference run's 21 ms video start offset comes from.
    """

    def test_the_audio_gains_at_most_one_aac_frame(self, three):
        overshoot = three["joined"]["audio_s"] - three["requested_s"]
        assert 0.0 <= overshoot <= AAC_FRAME_S + 0.001

    def test_the_video_stream_starts_at_most_one_aac_frame_late(self, three):
        assert 0.0 <= three["joined"]["video_start_s"] <= AAC_FRAME_S + 0.001

    def test_audio_and_video_end_within_one_frame_of_each_other(self, three):
        divergence = abs(three["joined"]["audio_s"] - three["joined"]["video_s"])
        assert divergence <= FRAME_S


class TestTheOverheadDoesNotAccumulate:
    """The one that matters for an hour-long video.

    Doubling the chunk count must not double the error. Asserted as a
    comparison rather than against a constant, so it stays true if ffmpeg's
    fixed cost ever changes, and false the moment that cost becomes
    per-boundary.
    """

    def test_six_chunks_cost_the_same_as_three(self, three, six):
        three_cost = three["joined"]["audio_s"] - three["requested_s"]
        six_cost = six["joined"]["audio_s"] - six["requested_s"]
        assert six_cost == pytest.approx(three_cost, abs=0.005)

    def test_the_error_is_not_proportional_to_the_chunk_count(self, six):
        # If it were per-boundary, six clips would cost roughly twice three.
        # The reference run's 3 chunks over 11 minutes is the case this protects.
        assert six["joined"]["audio_s"] - six["requested_s"] < 2 * AAC_FRAME_S

    def test_the_video_stays_exact_however_many_chunks(self, six):
        assert six["joined"]["video_s"] == pytest.approx(six["requested_s"], abs=0.005)


class TestSegmentsWithinAChunk:
    """Which subtitles a chunk burns. F7 depends on an F4 guarantee here.

    F4 cuts chunks on Whisper segment edges, so no segment straddles a boundary.
    `render.segments_within` would return a straddling segment to both chunks,
    and the joined video would show that line twice. The path is unreachable
    while F4 holds; these tests pin both halves of that sentence, because a
    change to either side turns a latent defect into a visible one.
    """

    SEGMENTS = [
        {"start": 0.0, "end": 2.0, "text": "one"},
        {"start": 2.0, "end": 5.0, "text": "two"},
        {"start": 5.0, "end": 9.0, "text": "three"},
        {"start": 9.0, "end": 12.0, "text": "four"},
    ]

    def test_an_edge_aligned_boundary_puts_each_segment_in_exactly_one_chunk(self):
        first = render.segments_within(self.SEGMENTS, 0.0, 5.0)
        second = render.segments_within(self.SEGMENTS, 5.0, 12.0)
        assert [s["text"] for s in first] == ["one", "two"]
        assert [s["text"] for s in second] == ["three", "four"]
        # Every segment placed, none placed twice.
        assert len(first) + len(second) == len(self.SEGMENTS)

    def test_a_segment_ending_exactly_where_the_chunk_begins_is_not_repeated(self):
        # The `>` that does the work. With `>=` this segment lands in both.
        kept = render.segments_within(self.SEGMENTS, 2.0, 5.0)
        assert [s["text"] for s in kept] == ["two"]

    def test_a_segment_starting_exactly_where_the_chunk_ends_is_not_pulled_in(self):
        kept = render.segments_within(self.SEGMENTS, 0.0, 2.0)
        assert [s["text"] for s in kept] == ["one"]

    def test_a_straddling_segment_is_still_given_to_both_chunks(self):
        # Documented, not desired: half a subtitle is worse than a repeated one.
        # If this starts happening on real data then F4 changed, and F7 needs the
        # decision revisited - it is not a render bug to fix in isolation.
        straddling = [{"start": 4.0, "end": 6.0, "text": "across"}]
        assert render.segments_within(straddling, 0.0, 5.0)
        assert render.segments_within(straddling, 5.0, 12.0)

    def test_a_chunk_with_no_segments_burns_no_subtitles(self):
        assert render.segments_within(self.SEGMENTS, 20.0, 30.0) == []

    def test_an_empty_transcript_is_not_an_error(self):
        assert render.segments_within([], 0.0, 5.0) == []
