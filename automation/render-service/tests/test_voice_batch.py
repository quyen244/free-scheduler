"""Batched synthesis, with no model behind it.

`test_voice.py` proves the whole path against the real VieNeu graph, which
costs a GPU and minutes a run. What is left for here is everything that has to
be true *regardless* of what the model returns: that grouping never reorders
the track, that progress only climbs, that a short batch fails the job instead
of leaving a silent hole in it, and that a voice alias resolves before any
weights load.

Only the model is faked, and only at the seam where it is held. Everything
below that - grouping, trimming, the atempo subprocess, reassembly - is the
real code, because batching is what is new and batching is what these tests
exist to hold still.

This replaced test_voice_pool.py when the ONNX session pool did: there are no
sessions, workers or threads to test any more.
"""

import dataclasses
from pathlib import Path

import numpy as np
import pytest

import timing
import voice
from errors import SynthesisError, UnknownVoiceError

# Nothing here reaches a service, so the suite-wide video preconditions are
# pure cost. See `_pipeline_preconditions` in conftest.py.
pytestmark = pytest.mark.no_pipeline

VOICE = "Minh Quân Pro"

# Deliberately uneven, and long enough in places to need a speed-up: a run that
# reassembles by arrival instead of by index produces a visibly different
# track. The empty one is a Whisper music/applause segment.
SEGMENTS = [
    {"start": 0.0, "end": 2.0, "text": "một"},
    {"start": 2.0, "end": 4.0, "text": ""},
    {"start": 4.0, "end": 6.0, "text": "ba ba ba ba ba ba ba ba ba ba ba ba"},
    {"start": 6.0, "end": 8.0, "text": "bốn"},
    {"start": 8.0, "end": 10.0, "text": "năm năm năm năm năm năm năm năm năm"},
    {"start": 10.0, "end": 12.0, "text": "sáu"},
]


def fake_speech(text: str) -> np.ndarray:
    """Deterministic audio whose length follows the text.

    A tone, not silence: `trim_silence` drops everything under its threshold,
    so silence here would make every segment zero-length and prove nothing.
    """
    samples = int(timing.SAMPLE_RATE * 0.25 * max(len(text.split()), 1))
    t = np.arange(samples, dtype=np.float32) / timing.SAMPLE_RATE
    return 0.5 * np.sin(2 * np.pi * 220.0 * t)


class FakeModel:
    """Stands in for a loaded VieNeu model, call shape included."""

    sample_rate = timing.SAMPLE_RATE

    def __init__(self, *, short_by: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.batch_sizes: list[int] = []
        self.voices: list[str] = []
        self._short_by = short_by

    def infer_batch(self, texts, voice=None, batch_size=None, **kwargs):
        self.calls.append(list(texts))
        self.batch_sizes.append(batch_size)
        self.voices.append(voice)
        wavs = [fake_speech(text) for text in texts]
        return wavs[: len(wavs) - self._short_by] if self._short_by else wavs


@pytest.fixture
def model(monkeypatch) -> FakeModel:
    fake = FakeModel()
    monkeypatch.setattr(voice, "_model", fake)
    return fake


@pytest.fixture(autouse=True)
def _drop_model():
    yield
    voice.reset_model()


def _use(monkeypatch, **fields) -> None:
    """`Settings` is a frozen dataclass, so swap the whole object."""
    monkeypatch.setattr(
        voice, "settings", dataclasses.replace(voice.settings, **fields)
    )


def _speak(tmp_path: Path, on_progress=None, group_size=None):
    return voice.speak_segments(
        SEGMENTS, VOICE, tmp_path, on_progress, group_size=group_size
    )


class TestOrdering:
    def test_fits_come_back_in_segment_order(self, model, tmp_path):
        fits, _ = _speak(tmp_path)
        assert [fit.idx for fit in fits] == list(range(len(SEGMENTS)))

    def test_pieces_are_placed_at_their_own_start_times(self, model, tmp_path):
        _, pieces = _speak(tmp_path)
        # The empty segment contributes no audio, so the spoken five are what
        # is left - and they must still be in ascending time order.
        assert [start for start, _ in pieces] == [0.0, 4.0, 6.0, 8.0, 10.0]

    def test_group_size_does_not_change_the_track(self, model, tmp_path):
        one_call, pieces_a = _speak(tmp_path, group_size=64)
        voice.reset_model()
        second = FakeModel()
        voice._model = second
        many_calls, pieces_b = _speak(tmp_path, group_size=1)

        assert [f.idx for f in one_call] == [f.idx for f in many_calls]
        assert [f.raw_s for f in one_call] == [f.raw_s for f in many_calls]
        for (start_a, audio_a), (start_b, audio_b) in zip(pieces_a, pieces_b):
            assert start_a == start_b
            assert np.array_equal(audio_a, audio_b)
        # And they really did take different routes to the same answer.
        assert len(model.calls) == 1
        assert len(second.calls) == 5


class TestEmptySegments:
    def test_a_segment_with_no_text_never_reaches_the_model(self, model, tmp_path):
        _speak(tmp_path)
        sent = [text for call in model.calls for text in call]
        assert "" not in sent
        assert len(sent) == 5

    def test_a_segment_with_no_text_is_still_reported(self, model, tmp_path):
        fits, _ = _speak(tmp_path)
        assert fits[1].warning is not None
        assert "no text to speak" in fits[1].warning
        assert fits[1].raw_s == 0.0


class TestBatching:
    def test_the_configured_batch_size_reaches_the_model(
        self, model, tmp_path, monkeypatch
    ):
        _use(monkeypatch, tts_batch_size=32)
        _speak(tmp_path)
        assert model.batch_sizes == [32]

    def test_groups_default_to_twice_the_batch_size(
        self, model, tmp_path, monkeypatch
    ):
        # Two spoken segments per call out of five means three calls.
        _use(monkeypatch, tts_batch_size=1)
        _speak(tmp_path)
        assert [len(call) for call in model.calls] == [2, 2, 1]

    def test_the_resolved_voice_reaches_the_model(self, model, tmp_path):
        _speak(tmp_path)
        assert model.voices == [VOICE]

    def test_a_short_batch_fails_the_job(self, monkeypatch, tmp_path):
        # Padding to length would put silence where speech belongs and leave
        # the track looking complete.
        monkeypatch.setattr(voice, "_model", FakeModel(short_by=1))
        with pytest.raises(SynthesisError, match="got"):
            _speak(tmp_path)


class TestProgress:
    def test_progress_only_climbs_and_reaches_one(self, model, tmp_path):
        seen: list[float] = []
        _speak(tmp_path, on_progress=seen.append, group_size=2)
        assert seen == sorted(seen)
        assert seen[-1] == pytest.approx(1.0)
        assert all(0.0 < value <= 1.0 for value in seen)

    def test_the_silent_segment_counts_towards_progress(self, model, tmp_path):
        seen: list[float] = []
        _speak(tmp_path, on_progress=seen.append, group_size=64)
        # One report for the empty segment before any synthesis, one after the
        # single group - and the first must already account for the silence.
        assert seen[0] == pytest.approx(1 / len(SEGMENTS))
        assert seen[-1] == pytest.approx(1.0)


class TestVoiceValidation:
    def test_an_alias_resolves_to_its_preset(self):
        assert voice.check_voice("Minh Quân") == "Minh Quân Pro"

    def test_a_preset_resolves_to_itself(self):
        assert voice.check_voice("Minh Quân Pro") == "Minh Quân Pro"

    def test_a_zerotts_voice_is_refused(self):
        # The engines' voice sets do not overlap, so an old manifest's voice is
        # now a typo rather than a silent substitution.
        with pytest.raises(UnknownVoiceError, match="unknown voice"):
            voice.check_voice("maichi")

    def test_the_refusal_lists_the_real_presets(self):
        with pytest.raises(UnknownVoiceError, match="Minh Quân Pro"):
            voice.check_voice("my-own-voice")

    def test_validation_does_not_load_the_model(self):
        voice.check_voice("Minh Quân")
        assert not voice.is_loaded()
