"""F5 — translate zh/en → vi.

The thing that has to hold is alignment. A generative model asked to translate
a list can merge two lines into one, echo the prompt, or return an empty
string, and a segment list that comes back one short shifts every subtitle
after it. That does not look like a bug until three stages later, when the
voice track is out of sync with the picture and nobody knows why.

So: same count, byte-identical timings, or the job fails.

The service half of this file talks to the real uvicorn process over a real
socket, not through `TestClient`. `TestClient` runs background work inside the
request it was started from, so a 202 measured through it would look instant
however the endpoint was written. See `media-service/tests/test_jobs.py`.
"""

import dataclasses
import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

import budget
import translator
from config import settings
from errors import (
    MisalignedTranslationError,
    TranslationError,
    TruncatedTranslationError,
)
from main import app
from shared import pipeline_db

# uvicorn, in this same container.
SERVICE = "http://127.0.0.1:8002"

# The 11-minute English source F4 chunks — long enough that the async design
# has something to prove: 104 segments at ~1.5 s each is well past the 300 s
# n8n gives a synchronous HTTP node.
TEST_VIDEO_ID = "3gi_15UH9fQ"
TEST_VIDEO_URL = "https://www.youtube.com/watch?v=3gi_15UH9fQ"

SHORT_VIDEO_ID = "jNQXAC9IVRw"
SHORT_VIDEO_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"

MEDIA_SERVICE = "http://media-service:8001"
TRANSCRIPT_SERVICE = "http://whisper-transcript-service:8000"

JOB_TIMEOUT_S = 1800.0


class _Recorder(HTTPServer):
    received: list[dict]


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.server.received.append(json.loads(body))  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args: object) -> None:
        pass


@contextmanager
def callback_recorder() -> Iterator[_Recorder]:
    """A real HTTP endpoint standing in for n8n's Wait-node resume URL."""
    server = _Recorder(("127.0.0.1", 0), _Handler)
    server.received = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def _resume_url(recorder: _Recorder) -> str:
    return f"http://127.0.0.1:{recorder.server_port}/webhook-waiting/test"


def _await_callback(recorder: _Recorder, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if recorder.received:
            return recorder.received[0]
        time.sleep(1.0)
    raise AssertionError(f"no callback arrived within {timeout}s")


@pytest.fixture(scope="module")
def transcribed() -> str:
    """The fixture video, ingested and transcribed by the services that own it."""
    httpx.post(
        f"{MEDIA_SERVICE}/download", json={"url": TEST_VIDEO_URL}, timeout=900
    ).raise_for_status()
    httpx.post(
        f"{TRANSCRIPT_SERVICE}/transcribe",
        json={"video_id": TEST_VIDEO_ID},
        timeout=900,
    ).raise_for_status()
    return TEST_VIDEO_ID


@pytest.fixture(scope="module")
def short_transcribed() -> str:
    """"Me at the zoo" — 19 seconds, a handful of segments.

    Used where the point is the shape of the run rather than the volume of it,
    so a test that needs two translations end to end costs seconds.
    """
    httpx.post(
        f"{MEDIA_SERVICE}/download", json={"url": SHORT_VIDEO_URL}, timeout=900
    ).raise_for_status()
    httpx.post(
        f"{TRANSCRIPT_SERVICE}/transcribe",
        json={"video_id": SHORT_VIDEO_ID},
        timeout=900,
    ).raise_for_status()
    return SHORT_VIDEO_ID


# --- alignment, without the model -----------------------------------------


def test_a_merged_reply_is_rejected_rather_than_returned(monkeypatch):
    segments = [
        {"start": 0.0, "end": 1.0, "text": "one"},
        {"start": 1.0, "end": 2.0, "text": "two"},
    ]
    # The failure that matters: the model folds two lines into one.
    monkeypatch.setattr(translator, "translate_lines", lambda lines, source: ["một hai"])

    with pytest.raises(MisalignedTranslationError):
        translator.translate_segments(segments, "en")


def test_an_empty_translation_is_rejected(monkeypatch):
    segments = [{"start": 0.0, "end": 1.0, "text": "one"}]
    monkeypatch.setattr(translator, "translate_lines", lambda lines, source: ["   "])

    with pytest.raises(MisalignedTranslationError):
        translator.translate_segments(segments, "en")


def test_timings_are_copied_not_regenerated(monkeypatch):
    segments = [
        {"start": 0.0, "end": 1.5, "text": "one"},
        {"start": 1.5, "end": 3.25, "text": "two"},
    ]
    monkeypatch.setattr(
        translator, "translate_lines", lambda lines, source: ["một", "hai"]
    )

    out = translator.translate_segments(segments, "en")

    assert [(s["start"], s["end"]) for s in out] == [(0.0, 1.5), (1.5, 3.25)]
    assert [s["text"] for s in out] == ["một", "hai"]


def test_a_vietnamese_source_is_not_sent_to_the_model(monkeypatch):
    # Criterion 9 is really a property of the n8n branch, but a service that
    # would happily re-translate Vietnamese into Vietnamese when called by hand
    # is one bad IF condition away from garbling a whole video.
    def explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("the model was asked to translate Vietnamese")

    monkeypatch.setattr(translator, "translate_lines", explode)

    out = translator.translate_segments(
        [{"start": 0.0, "end": 1.0, "text": "xin chào"}], "vi"
    )

    assert out == [{"start": 0.0, "end": 1.0, "text": "xin chào"}]


def test_a_narrated_reply_is_stripped_back_to_the_translation():
    assert translator._clean('Translation: "một hai"') == "một hai"
    assert translator._clean("  Bản dịch: một hai  ") == "một hai"
    assert translator._clean("một hai") == "một hai"


# --- the service ----------------------------------------------------------


def test_health_answers_before_the_model_is_loaded(monkeypatch):
    # 1.1 GB of weights load on the first job, not at startup: holding the
    # healthcheck down for that on every restart buys nothing.
    monkeypatch.setattr(translator, "_model", None)

    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "model_loaded": False}


def test_translate_preserves_the_segment_count_and_the_timings(transcribed):
    source = json.loads(
        (settings.data_dir / transcribed / "transcript.json").read_text(encoding="utf-8")
    )

    with callback_recorder() as recorder:
        started = time.monotonic()
        response = httpx.post(
            f"{SERVICE}/translate/jobs",
            json={"video_id": transcribed, "callback_url": _resume_url(recorder)},
            timeout=30.0,
        )
        accepted_after = time.monotonic() - started

        assert response.status_code == 202
        # The whole point of the job: the HTTP call does not bound the work.
        assert accepted_after < 5.0

        callback = _await_callback(recorder, JOB_TIMEOUT_S)

    assert callback["state"] == "done", callback["error"]
    assert callback["result"]["total_segments"] == len(source["segments"])

    translated = json.loads(
        (settings.data_dir / transcribed / "transcript.vi.json").read_text(
            encoding="utf-8"
        )
    )
    assert translated["language"] == "vi"
    assert translated["source_language"] == source["language"]
    assert len(translated["segments"]) == len(source["segments"])
    for before, after in zip(source["segments"], translated["segments"]):
        assert after["start"] == before["start"]
        assert after["end"] == before["end"]
        assert after["text"].strip() != ""
    # Actually translated, not passed through.
    assert [s["text"] for s in translated["segments"]] != [
        s["text"] for s in source["segments"]
    ]

    with pipeline_db.connect() as db:
        stage = db.execute(
            "SELECT stage FROM videos WHERE video_id = ?", (transcribed,)
        ).fetchone()["stage"]
    assert stage in pipeline_db.STAGE_ORDER[pipeline_db.STAGE_ORDER.index("translated"):]


def test_the_finished_job_is_readable_afterwards(short_transcribed):
    with callback_recorder() as recorder:
        response = httpx.post(
            f"{SERVICE}/translate/jobs",
            json={
                "video_id": short_transcribed,
                "callback_url": _resume_url(recorder),
            },
            timeout=30.0,
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        # Awaited rather than left running: a test that walks away from a job
        # leaves a row in flight for whatever runs next.
        callback = _await_callback(recorder, 600.0)

    # Same record from both surfaces, so a workflow that missed its callback can
    # still find out what happened.
    read_back = httpx.get(f"{SERVICE}/jobs/{job_id}", timeout=10.0).json()
    assert read_back["job_id"] == job_id
    assert read_back["kind"] == "translate"
    assert read_back["state"] == callback["state"] == "done"
    assert read_back["result"] == callback["result"]

    assert httpx.get(f"{SERVICE}/jobs/nosuchjob", timeout=10.0).status_code == 404


def test_two_jobs_at_once_both_finish(short_transcribed):
    """One llama.cpp context, a thread per background job.

    Two jobs generating on the same context at the same time wedged the whole
    process: no error, no progress, both rows stuck at `running`, and `/health`
    stopped answering — so even the container healthcheck could not see it. Two
    videos in flight is not exotic; it is what a second webhook call does.
    """
    with callback_recorder() as first, callback_recorder() as second:
        for recorder in (first, second):
            accepted = httpx.post(
                f"{SERVICE}/translate/jobs",
                json={
                    "video_id": short_transcribed,
                    "callback_url": _resume_url(recorder),
                },
                timeout=30.0,
            )
            assert accepted.status_code == 202

        for recorder in (first, second):
            assert _await_callback(recorder, 600.0)["state"] == "done"


def test_translate_refuses_a_video_that_was_never_transcribed():
    response = httpx.post(
        f"{SERVICE}/translate/jobs", json={"video_id": "aaaaaaaaaaa"}, timeout=10.0
    )

    assert response.status_code == 404
    assert "transcript" in response.json()["error"]


def test_translate_rejects_a_video_id_that_is_not_the_right_shape():
    response = httpx.post(
        f"{SERVICE}/translate/jobs", json={"video_id": "../../etc/passwd"}, timeout=10.0
    )

    assert response.status_code == 400


# --- the slot budget, without the model -----------------------------------
#
# `_fit_to_slots` calls `_translate_line` directly rather than `translate_lines`,
# so these patch the single-line call and count how often it is reached.


def _without_budget(**changes):
    """A settings copy. `settings` is frozen, so it cannot be patched in place."""
    return dataclasses.replace(translator.settings, **changes)


def _first_pass(monkeypatch, texts):
    monkeypatch.setattr(translator, "translate_lines", lambda lines, source: list(texts))


def test_a_segment_that_fits_its_slot_is_never_retried(monkeypatch):
    _first_pass(monkeypatch, ["ngắn"])

    def explode(*args: object, **kwargs: object) -> str:
        raise AssertionError("a segment that fits was sent back to the model")

    monkeypatch.setattr(translator, "_translate_line", explode)

    out = translator.translate_segments([{"start": 0.0, "end": 8.0, "text": "short"}], "en")

    assert out[0]["text"] == "ngắn"


def test_a_segment_that_overruns_its_slot_is_retried_and_shortened(monkeypatch):
    long = "a" * 300
    _first_pass(monkeypatch, [long])
    calls = []

    def retry(text, source, budget_chars=None, max_tokens=None, seed=None):
        calls.append((budget_chars, max_tokens, seed))
        return "b" * 100

    monkeypatch.setattr(translator, "_translate_line", retry)

    out = translator.translate_segments([{"start": 0.0, "end": 6.0, "text": "src"}], "en")

    assert out[0]["text"] == "b" * 100
    assert len(calls) == 1
    # The retry is told a budget derived from its own slot, and a token cap.
    assert calls[0][0] == budget.chars_for_slot(6.0, translator.settings.target_ratio)
    assert 0 < calls[0][1] <= translator.settings.max_output_tokens
    # A seed the first pass does not use, so the retry can differ from it.
    assert calls[0][2] in translator._RETRY_SEEDS


def test_a_retry_that_comes_back_longer_is_discarded(monkeypatch):
    first = "a" * 300
    _first_pass(monkeypatch, [first])
    monkeypatch.setattr(
        translator, "_translate_line", lambda *a, **k: "b" * 400
    )

    out = translator.translate_segments([{"start": 0.0, "end": 6.0, "text": "src"}], "en")

    assert out[0]["text"] == first


def test_a_retry_that_fails_does_not_fail_the_job(monkeypatch):
    first = "a" * 300
    _first_pass(monkeypatch, [first])

    def boom(*args: object, **kwargs: object) -> str:
        raise TranslationError("the model fell over")

    monkeypatch.setattr(translator, "_translate_line", boom)

    # The first pass already succeeded, so the stage must still deliver.
    out = translator.translate_segments([{"start": 0.0, "end": 6.0, "text": "src"}], "en")

    assert out[0]["text"] == first


def test_a_truncated_retry_is_never_shipped(monkeypatch):
    # The one outcome that would be worse than a rushed segment: a subtitle cut
    # off in the middle of a sentence.
    first = "a" * 300
    _first_pass(monkeypatch, [first])

    def truncated(*args: object, **kwargs: object) -> str:
        raise TruncatedTranslationError("hit the cap")

    monkeypatch.setattr(translator, "_translate_line", truncated)

    out = translator.translate_segments([{"start": 0.0, "end": 6.0, "text": "src"}], "en")

    assert out[0]["text"] == first


def test_an_empty_retry_is_never_shipped(monkeypatch):
    first = "a" * 300
    _first_pass(monkeypatch, [first])
    monkeypatch.setattr(translator, "_translate_line", lambda *a, **k: "   ")

    out = translator.translate_segments([{"start": 0.0, "end": 6.0, "text": "src"}], "en")

    assert out[0]["text"] == first


def test_the_budget_pass_never_moves_a_timestamp(monkeypatch):
    # The invariant the whole pipeline rests on. Shortening text may change the
    # words; it must never change when they are said.
    segments = [
        {"start": 0.0, "end": 6.0, "text": "one"},
        {"start": 6.0, "end": 12.5, "text": "two"},
        {"start": 12.5, "end": 19.25, "text": "three"},
    ]
    _first_pass(monkeypatch, ["a" * 300, "b" * 20, "c" * 400])
    monkeypatch.setattr(translator, "_translate_line", lambda *a, **k: "short")

    out = translator.translate_segments(segments, "en")

    assert [(s["start"], s["end"]) for s in out] == [
        (0.0, 6.0),
        (6.0, 12.5),
        (12.5, 19.25),
    ]
    assert len(out) == len(segments)
    # The middle one fitted, so it kept its first-pass text untouched.
    assert out[1]["text"] == "b" * 20


def test_the_budget_pass_can_be_switched_off(monkeypatch):
    # The off switch is what makes a before/after benchmark one variable.
    _first_pass(monkeypatch, ["a" * 300])
    monkeypatch.setattr(translator, "settings", _without_budget(budget_enabled=False))

    def explode(*args: object, **kwargs: object) -> str:
        raise AssertionError("the budget pass ran while it was switched off")

    monkeypatch.setattr(translator, "_translate_line", explode)

    out = translator.translate_segments([{"start": 0.0, "end": 6.0, "text": "src"}], "en")

    assert out[0]["text"] == "a" * 300


def test_the_report_records_what_was_decided(monkeypatch):
    _first_pass(monkeypatch, ["a" * 300])
    monkeypatch.setattr(translator, "_translate_line", lambda *a, **k: "b" * 100)
    report: list = []

    translator.translate_segments(
        [{"start": 0.0, "end": 6.0, "text": "src"}], "en", report
    )

    assert len(report) == 1
    assert report[0]["idx"] == 0
    assert report[0]["before_chars"] == 300
    assert report[0]["after_chars"] == 100
    assert "shorter" in report[0]["decision"]


def test_the_budget_reaches_the_prompt(monkeypatch):
    """The retry must actually tell the model the number, not just cap tokens."""
    seen = {}

    class FakeModel:
        def create_chat_completion(self, messages, max_tokens, **sampling):
            seen["content"] = messages[0]["content"]
            seen["max_tokens"] = max_tokens
            return {
                "choices": [
                    {"message": {"content": "ngắn"}, "finish_reason": "stop"}
                ]
            }

    monkeypatch.setattr(translator, "get_model", FakeModel)

    translator._translate_line("source text", "en", budget_chars=151, max_tokens=84)

    assert "151" in seen["content"]
    assert seen["max_tokens"] == 84


def test_a_retry_stopped_at_the_cap_raises_rather_than_returning_half(monkeypatch):
    class FakeModel:
        def create_chat_completion(self, messages, max_tokens, **sampling):
            return {
                "choices": [
                    {"message": {"content": "một nửa câu"}, "finish_reason": "length"}
                ]
            }

    monkeypatch.setattr(translator, "get_model", FakeModel)

    with pytest.raises(TruncatedTranslationError):
        translator._translate_line("source", "en", budget_chars=151, max_tokens=84)


def test_the_first_pass_still_tolerates_a_long_generation(monkeypatch):
    # Unchanged behaviour on purpose: raising here would fail videos that
    # succeed today.
    class FakeModel:
        def create_chat_completion(self, messages, max_tokens, **sampling):
            return {
                "choices": [
                    {"message": {"content": "một câu dài"}, "finish_reason": "length"}
                ]
            }

    monkeypatch.setattr(translator, "get_model", FakeModel)

    assert translator._translate_line("source", "en") == "một câu dài"


# --- Reproducibility -------------------------------------------------------
#
# `_SEED` is handed to `Llama(...)` once, at construction. llama.cpp seeds its
# RNG there and then keeps advancing it, so a call's output depends on every
# call made before it, not only on its own text. Measured in the container: the
# same sentence translated twice in one process came back 99 and 95 characters
# long; with a seed passed per call it came back identical both times.
#
# That is what these pin. Without it "a re-run does not silently produce
# different subtitles" is a comment, not a property, and no before/after
# comparison of translated text has one variable in it.


class _RecordingModel:
    """Stands in for llama.cpp and keeps the kwargs it was called with."""

    def __init__(self, reply: str = "bản dịch") -> None:
        self.calls: list[dict] = []
        self._reply = reply

    def create_chat_completion(self, **kwargs: object) -> dict:
        self.calls.append(kwargs)
        return {"choices": [{"message": {"content": self._reply},
                             "finish_reason": "stop"}]}


def test_the_first_pass_seeds_every_call(monkeypatch):
    model = _RecordingModel()
    monkeypatch.setattr(translator, "get_model", lambda: model)

    translator._translate_line("the first sentence", "en")
    translator._translate_line("the second sentence", "en")

    assert [call["seed"] for call in model.calls] == [translator._SEED,
                                                      translator._SEED]


def test_the_same_segment_gets_the_same_seed_wherever_it_sits(monkeypatch):
    # The property that matters: a segment's translation must not depend on how
    # many segments came before it.
    model = _RecordingModel()
    monkeypatch.setattr(translator, "get_model", lambda: model)

    translator.translate_lines(["one", "two", "three"], "en")

    seeds = {call["seed"] for call in model.calls}
    assert seeds == {translator._SEED}


def test_a_retry_keeps_its_own_seed(monkeypatch):
    # The budget pass deliberately varies the seed - that is the whole point of
    # a second attempt - so the fix above must not flatten it back to `_SEED`.
    model = _RecordingModel()
    monkeypatch.setattr(translator, "get_model", lambda: model)

    translator._translate_line("src", "en", budget_chars=40, max_tokens=64,
                               seed=20260907)

    assert model.calls[0]["seed"] == 20260907
