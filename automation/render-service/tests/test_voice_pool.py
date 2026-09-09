"""The session pool and parallel synthesis, with no model behind it.

`test_voice.py` proves the whole path against the real ZeroTTS graph, which
costs minutes a run. What is left for here is everything that has to be true
*regardless* of what the model returns: that more workers do not change the
track, that progress only ever climbs, that one bad segment fails the job
instead of hanging it, and that the pool never builds more sessions than it
was allowed.

Only the model is faked, and only at the seam where it is loaded. Everything
below that - borrowing a session, the atempo subprocess, reassembly - is the
real code, because it is the concurrency that is new and the concurrency is
what these tests exist to hold still.
"""

import threading
import time
from pathlib import Path

import numpy as np
import pytest

import timing
import voice
from errors import SynthesisError

# Nothing here reaches a service, so the suite-wide video preconditions are
# pure cost. See `_pipeline_preconditions` in conftest.py.
pytestmark = pytest.mark.no_pipeline

VOICE = "maichi"

# Deliberately uneven, and long enough in places to need a speed-up: a run that
# reassembles by completion order instead of by index produces a visibly
# different track, and the fastest segments finish first.
SEGMENTS = [
    {"start": 0.0, "end": 2.0, "text": "một"},
    {"start": 2.0, "end": 4.0, "text": ""},
    {"start": 4.0, "end": 6.0, "text": "ba ba ba ba ba ba ba ba ba ba ba ba"},
    {"start": 6.0, "end": 8.0, "text": "bốn"},
    {"start": 8.0, "end": 10.0, "text": "năm năm năm năm năm năm năm năm năm"},
    {"start": 10.0, "end": 12.0, "text": "sáu"},
]
TOTAL_S = 12.0


def fake_speech(text: str) -> np.ndarray:
    """Deterministic audio whose length follows the text.

    A tone, not silence: `trim_silence` drops everything under its threshold,
    so silence here would make every segment zero-length and the test would
    prove nothing.
    """
    samples = int(timing.SAMPLE_RATE * 0.25 * max(len(text.split()), 1))
    t = np.arange(samples, dtype=np.float32) / timing.SAMPLE_RATE
    return 0.5 * np.sin(2 * np.pi * 220.0 * t)


class FakeSession:
    """Stands in for a ZeroTTS session, shape included.

    `synthesize` returns (1, N) exactly as the real one does - the bug that
    read that as one sample is worth keeping a test honest about.
    """

    def __init__(self, fails_on: str | None = None, delay_s: float = 0.0):
        self.fails_on = fails_on
        self.delay_s = delay_s
        self.calls = 0

    def synthesize(self, text: str, voice: str | None = None) -> np.ndarray:
        self.calls += 1
        if self.fails_on and self.fails_on in text:
            raise RuntimeError("the model gave up on " + repr(text[:20]))
        if self.delay_s:
            time.sleep(self.delay_s)
        return fake_speech(text)[None, :]


@pytest.fixture
def sessions(monkeypatch):
    """Every session the pool builds, in the order it built them."""
    built: list[FakeSession] = []

    def load():
        built.append(FakeSession())
        return built[-1]

    monkeypatch.setattr(voice, "_load_model", load)
    # The real one imports zerotts, which pulls in onnxruntime.
    monkeypatch.setattr(voice, "normalise", lambda text: text)
    voice.reset_pool()
    yield built
    voice.reset_pool()


def speak(workspace: Path, workers: int, on_progress=None):
    workspace.mkdir(parents=True, exist_ok=True)
    return voice.speak_segments(SEGMENTS, VOICE, workspace, on_progress, workers=workers)


def test_more_workers_do_not_change_the_track(sessions, tmp_path):
    """The whole safety argument for parallelising this stage.

    Segments are independent, so the only thing concurrency can break is the
    order they are reassembled in - which is exactly what this compares.
    """
    one_fits, one_pieces = speak(tmp_path / "one", 1)
    many_fits, many_pieces = speak(tmp_path / "many", 4)

    assert [fit.idx for fit in one_fits] == [fit.idx for fit in many_fits]
    assert one_fits == many_fits

    assert [start for start, _ in one_pieces] == [start for start, _ in many_pieces]
    for (_, left), (_, right) in zip(one_pieces, many_pieces):
        assert np.array_equal(left, right)

    assert np.array_equal(timing.assemble(one_pieces, TOTAL_S),
                          timing.assemble(many_pieces, TOTAL_S))


def test_a_segment_with_no_text_keeps_its_place(sessions, tmp_path):
    fits, pieces = speak(tmp_path, 4)

    assert len(fits) == len(SEGMENTS)
    silent = fits[1]
    assert silent.idx == 1
    assert silent.raw_s == 0.0
    assert "no text to speak" in (silent.warning or "")
    # It holds a slot and a warning, but contributes no audio.
    assert len(pieces) == len(SEGMENTS) - 1


def test_a_segment_too_long_for_its_slot_is_still_sped_up(sessions, tmp_path):
    """The atempo subprocess has to survive being called from a worker thread."""
    fits, pieces = speak(tmp_path, 4)

    stretched = [fit for fit in fits if fit.atempo > 1.0]
    assert stretched, "the fixture no longer exercises the speed-up path"
    for fit in stretched:
        piece = dict((f.idx, samples) for f, (_, samples)
                     in zip([f for f in fits if f.raw_s > 0], pieces))[fit.idx]
        assert piece.size < fit.raw_s * timing.SAMPLE_RATE


def test_progress_only_ever_climbs_and_finishes_at_one(sessions, tmp_path):
    seen: list[float] = []
    lock = threading.Lock()

    def record(done: float) -> None:
        with lock:
            seen.append(done)

    speak(tmp_path, 4, on_progress=record)

    assert seen, "the job reported no progress at all"
    assert seen == sorted(seen), "progress went backwards: " + str(seen)
    assert seen[-1] == pytest.approx(1.0)
    assert all(0.0 < value <= 1.0 for value in seen)


def test_one_failed_segment_fails_the_whole_job(sessions, tmp_path, monkeypatch):
    """And does not hang waiting for the segments queued behind it."""
    def load_failing():
        return FakeSession(fails_on="ba", delay_s=0.05)

    monkeypatch.setattr(voice, "_load_model", load_failing)
    voice.reset_pool()

    started = time.time()
    with pytest.raises(SynthesisError):
        speak(tmp_path, 4)
    assert time.time() - started < 30, "the pool did not give up promptly"


def test_the_pool_never_builds_more_sessions_than_it_is_allowed(sessions, tmp_path):
    speak(tmp_path / "a", 2)
    assert len(sessions) <= 2

    # And it reuses them: a second run adds nothing.
    before = len(sessions)
    speak(tmp_path / "b", 2)
    assert len(sessions) == before


def test_one_worker_builds_exactly_one_session(sessions, tmp_path):
    speak(tmp_path, 1)
    assert len(sessions) == 1


def test_the_pool_carries_on_when_a_later_session_cannot_be_built(
    sessions, tmp_path, monkeypatch
):
    """Out of memory on session two is a reason to slow down, not to fail.

    Docker has 7.5 GB for every container here. A second ONNX session is the
    first thing that will not fit, and losing a twenty-minute job to that would
    be a worse outcome than taking longer.
    """
    attempts = []

    def one_then_fail():
        attempts.append(1)
        if len(attempts) > 1:
            raise MemoryError("no room for another session")
        return FakeSession()

    monkeypatch.setattr(voice, "_load_model", one_then_fail)
    voice.reset_pool()

    fits, pieces = speak(tmp_path, 4)
    assert len(fits) == len(SEGMENTS)
    assert len(attempts) >= 2, "it never tried for a second session"


def test_the_first_session_failing_is_still_a_failure(sessions, tmp_path, monkeypatch):
    def never():
        raise MemoryError("no room at all")

    monkeypatch.setattr(voice, "_load_model", never)
    voice.reset_pool()

    with pytest.raises(SynthesisError):
        speak(tmp_path, 2)


def test_workers_below_one_is_treated_as_one(sessions, tmp_path):
    fits, _ = speak(tmp_path, 0)
    assert len(fits) == len(SEGMENTS)
    assert len(sessions) == 1
