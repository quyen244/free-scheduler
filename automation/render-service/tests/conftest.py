"""Shared fixtures for the tests that talk to the running service.

Not `TestClient`: it runs background work inside the request it was started
from, so a 202 measured through it would look instant no matter how the
endpoint was written. Voicing and rendering are the two longest jobs in the
pipeline, and the whole point of them being jobs is that the HTTP call stops
bounding the work — only a real socket to the real uvicorn process shows that.
"""

import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Iterator

import httpx
import pytest

from shared import pipeline_db

# uvicorn, in this same container. See the module docstring.
SERVICE = "http://127.0.0.1:8003"

# The services that own the earlier stages, over the compose network.
MEDIA = "http://media-service:8001"
TRANSCRIPT = "http://whisper-transcript-service:8000"
TRANSLATE = "http://translate-service:8002"

# 19 seconds, three segments. Short enough that a full voice-and-render round
# trip is a test rather than a coffee break.
SHORT_VIDEO_ID = "jNQXAC9IVRw"
SHORT_VIDEO_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"


@pytest.fixture(scope="session")
def short_video_is_ready() -> None:
    """Build this suite's preconditions instead of assuming them.

    The media-service suite deletes this video's directory and its `videos`
    row on purpose — it is testing acquisition, and a cache it did not fill is
    not a cache it can measure. So these tests passed only while they happened
    to run first, which is the kind of dependency that fails later on someone
    else's machine and looks like a render bug.

    Each stage is asked of the service that owns it, and each is a no-op when
    the work is already on disk.
    """
    httpx.post(f"{MEDIA}/download", json={"url": SHORT_VIDEO_URL}, timeout=600).raise_for_status()
    httpx.post(
        f"{TRANSCRIPT}/transcribe", json={"video_id": SHORT_VIDEO_ID}, timeout=900
    ).raise_for_status()
    # This source is English, so the pipeline translates it before voicing —
    # and so must this suite. Voicing English text through a Vietnamese model
    # would still produce a track, with different lengths and therefore
    # different warnings, which is not the thing being tested.
    _translate()
    httpx.post(
        f"{TRANSCRIPT}/chunk", json={"video_id": SHORT_VIDEO_ID}, timeout=120
    ).raise_for_status()
    assert pipeline_db.chunks_for(SHORT_VIDEO_ID), "chunking produced no rows"
    assert (Path("/data") / SHORT_VIDEO_ID / "transcript.vi.json").is_file()

    # Rendering needs a voice track, and test_render.py sorts before
    # test_voice.py — so relying on the voicing tests to leave one behind is
    # the same order dependency in a smaller form. Built here once, for both.
    accepted = httpx.post(f"{SERVICE}/voice/jobs", json={"video_id": SHORT_VIDEO_ID}, timeout=30)
    accepted.raise_for_status()
    job = await_job(accepted.json()["job_id"], timeout=900)
    assert job["state"] == "done", job["error"]


def _translate() -> None:
    accepted = httpx.post(
        f"{TRANSLATE}/translate/jobs", json={"video_id": SHORT_VIDEO_ID}, timeout=60
    )
    accepted.raise_for_status()
    job = await_job_at(TRANSLATE, accepted.json()["job_id"], timeout=600)
    assert job["state"] == "done", job["error"]


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


def resume_url(recorder: _Recorder) -> str:
    return f"http://127.0.0.1:{recorder.server_port}/webhook-waiting/test"


def await_callback(recorder: _Recorder, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if recorder.received:
            return recorder.received[0]
        time.sleep(0.5)
    raise AssertionError(f"no callback within {timeout}s")


def await_job_at(service: str, job_id: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = httpx.get(f"{service}/jobs/{job_id}", timeout=10).json()
        if job["state"] in ("done", "failed"):
            return job
        time.sleep(2.0)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


def await_job(job_id: str, timeout: float) -> dict:
    return await_job_at(SERVICE, job_id, timeout)


@pytest.fixture(autouse=True)
def _pipeline_preconditions(request: pytest.FixtureRequest) -> None:
    """Give the preconditions above to every test that touches the pipeline.

    A module that talks to no service opts out with
    `pytestmark = pytest.mark.no_pipeline`. The fixture ingests, transcribes,
    translates and voices a video, which is minutes of work that a model-free
    unit test never uses — and which fails with a read timeout when the other
    services are busy.

    Opt-out and not opt-in on purpose: a new test that forgets the marker gets
    the preconditions, which is the safe direction. Forgetting it the other way
    brings back the order dependency this fixture exists to remove.
    """
    if request.node.get_closest_marker("no_pipeline") is None:
        request.getfixturevalue("short_video_is_ready")
