"""The fit arithmetic, with no model behind it.

Every one of these describes a way a voice track drifts away from the picture.
They run in milliseconds because none of them needs 900 MB of ONNX — which is
the point of keeping the arithmetic in its own module.
"""

import numpy as np
import pytest

import timing

MIN_RATIO = 0.75
MAX_RATIO = 1.35


def fit(raw_s: float, start_s: float = 0.0, end_s: float = 4.0) -> timing.Fit:
    return timing.plan_fit(0, start_s, end_s, raw_s, MIN_RATIO, MAX_RATIO)


def test_speech_that_already_fits_is_left_alone():
    assert fit(raw_s=4.0).atempo == 1.0


def test_speech_shorter_than_its_slot_is_padded_not_slowed_down():
    # An atempo below 1.0 stretches every vowel to fill time the speaker did
    # not use. The slot is filled with silence instead, which sounds like a
    # pause because it is one — and the timing is identical either way.
    planned = fit(raw_s=3.4)
    assert planned.atempo == 1.0
    assert planned.ratio == pytest.approx(0.85)
    assert planned.warning is None


def test_speech_longer_than_its_slot_is_sped_up_to_fit_exactly():
    planned = fit(raw_s=5.0)
    assert planned.atempo == pytest.approx(1.25)
    assert planned.raw_s / planned.atempo == pytest.approx(planned.slot_s)


def test_a_segment_needing_more_than_the_limit_is_reported_but_still_fitted():
    # Reported, not clamped. Clamping would keep the voice listenable and put
    # it out of sync with the picture from that segment onwards, which is the
    # one failure this whole stage exists to prevent.
    planned = fit(raw_s=8.0)
    assert planned.atempo == pytest.approx(2.0)
    assert planned.warning is not None
    assert "above the 1.35x limit" in planned.warning


def test_a_segment_that_barely_covers_its_slot_is_reported_too():
    planned = fit(raw_s=1.0)
    assert planned.atempo == 1.0
    assert planned.warning is not None
    assert "25%" in planned.warning


def test_a_segment_with_no_duration_is_an_error_not_a_division_by_zero():
    with pytest.raises(ValueError):
        timing.plan_fit(3, 10.0, 10.0, 2.0, MIN_RATIO, MAX_RATIO)


def test_a_rate_above_ffmpegs_limit_becomes_a_chain():
    # ffmpeg's atempo takes 0.5-2.0 per instance. Handed 3.0 it fails the whole
    # command, forty minutes into a render.
    chain = timing.atempo_chain(3.0)
    assert len(chain) == 2
    product = 1.0
    for step in chain:
        product *= float(step.split("=")[1])
    assert product == pytest.approx(3.0)


def test_a_rate_of_one_needs_no_filter_at_all():
    assert timing.atempo_chain(1.0) == []


def test_leading_silence_is_trimmed_off():
    # Load-bearing: the fit places a segment at its Whisper start time, so
    # silence the model prepended would push every word late by that much.
    samples = np.concatenate([np.zeros(1000), np.full(500, 0.5), np.zeros(1000)])
    assert timing.trim_silence(samples).size == 500


def test_a_silent_segment_trims_to_nothing_rather_than_raising():
    assert timing.trim_silence(np.zeros(1000)).size == 0


def test_each_piece_lands_at_its_own_start_time():
    sr = timing.SAMPLE_RATE
    one = np.full(sr, 1000, dtype=np.int16)
    track = timing.assemble([(0.0, one), (5.0, one), (10.0, one)], total_s=12.0)

    assert track.size == 12 * sr
    for start in (0, 5, 10):
        assert track[start * sr] != 0
        assert track[start * sr - 1 if start else track.size - 1] == 0


def test_one_long_piece_cannot_shift_the_ones_after_it():
    # The failure that concatenation would produce: a segment synthesised long
    # pushes everything after it late, and the drift accumulates for the rest
    # of the video.
    sr = timing.SAMPLE_RATE
    track = timing.assemble(
        [(0.0, np.full(3 * sr, 1000, dtype=np.int16)), (2.0, np.full(sr, 2000, dtype=np.int16))],
        total_s=5.0,
    )
    assert track[2 * sr] == 2000


def test_a_piece_past_the_end_of_the_track_is_dropped_not_crashed():
    sr = timing.SAMPLE_RATE
    track = timing.assemble([(9.0, np.full(sr, 1000, dtype=np.int16))], total_s=5.0)
    assert track.size == 5 * sr


def test_a_track_survives_a_round_trip_through_a_wav(tmp_path):
    samples = timing.to_pcm16(np.sin(np.linspace(0, 100, timing.SAMPLE_RATE)).astype(np.float32))
    path = tmp_path / "voice.wav"
    timing.write_wav(path, samples)

    assert not (tmp_path / "voice.wav.part").exists()
    assert np.array_equal(timing.read_wav(path), samples)


def test_a_sample_over_full_scale_clips_instead_of_wrapping():
    # Without the clip, 1.001 wraps to full-scale negative — a click in the
    # middle of a word, on exactly the loudest segments.
    loud = np.array([1.5, -1.5], dtype=np.float32)
    assert timing.to_pcm16(loud).tolist() == [32767, -32767]
