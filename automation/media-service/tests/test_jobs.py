"""Async ingest, against the real running server.

These tests do not use `TestClient`. It runs background work inside the request
it was started from, so a 202 measured through it would look instant no matter
how the endpoint was written — it would pass on a purely synchronous
implementation. The point of this feature is that the HTTP call stops bounding
the work, and only a real socket to the real uvicorn process can show that.
"""

import json
import shutil
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Iterator

import httpx

import jobs
import media
from shared import pipeline_db

# uvicorn, in this same container. See the module docstring.
SERVICE = "http://127.0.0.1:8001"

TEST_VIDEO_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
TEST_VIDEO_ID = "jNQXAC9IVRw"

# Shaped like a YouTube id, owned by no video. Passes URL validation and fails
# inside yt-dlp — the failure that strands a workflow if nothing calls back.
DEAD_VIDEO_URL = "https://www.youtube.com/watch?v=aaaaaaaaaaa"


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
        time.sleep(0.25)
    raise AssertionError(f"no callback arrived within {timeout}s")


def test_policy_rejected_ingest_answers_at_once_and_calls_back_with_failure():
    shutil.rmtree(media.video_dir(TEST_VIDEO_ID), ignore_errors=True)

    with callback_recorder() as recorder:
        started = time.monotonic()
        response = httpx.post(
            f"{SERVICE}/media/jobs",
            json={
                "url": TEST_VIDEO_URL,
                "callback_url": _resume_url(recorder),
            },
            timeout=30.0,
        )
        answered_in = time.monotonic() - started

        assert response.status_code == 202
        accepted = response.json()
        assert accepted["state"] == "queued"
        assert accepted["video_id"] == TEST_VIDEO_ID

        # The measurement this feature exists for. The same download takes
        # seconds here and minutes on a real source video; either way the
        # answer must not wait for it.
        assert answered_in < 2.0

        payload = _await_callback(recorder, timeout=180.0)

    assert payload["job_id"] == accepted["job_id"]
    assert payload["state"] == "failed"
    assert payload["video_id"] == TEST_VIDEO_ID
    assert "SourcePolicyError" in payload["error"]

    # Pollable as well as pushed, so a dropped callback costs a query rather
    # than a re-download.
    status = httpx.get(f"{SERVICE}/jobs/{accepted['job_id']}", timeout=10.0)
    assert status.status_code == 200
    assert status.json()["state"] == "failed"

    stored = pipeline_db.get_job(accepted["job_id"])
    assert stored is not None
    assert stored["state"] == "failed"
    assert stored["finished_at"] is not None


def test_a_failed_ingest_calls_back_too_instead_of_parking_the_workflow():
    """The failure this feature is really about.

    A job that dies quietly leaves an n8n execution waiting on a resume that
    will never come. Nothing goes red, nothing alerts, and you find out days
    later by wondering where a video went.
    """
    with callback_recorder() as recorder:
        response = httpx.post(
            f"{SERVICE}/media/jobs",
            json={
                "url": DEAD_VIDEO_URL,
                "callback_url": _resume_url(recorder),
            },
            timeout=30.0,
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]

        payload = _await_callback(recorder, timeout=120.0)

    assert payload["job_id"] == job_id
    assert payload["state"] == "failed"
    assert payload["result"] is None
    # The message has to name something, or the Telegram alert reads
    # "something failed" and the operator opens a terminal anyway.
    assert payload["error"]

    stored = pipeline_db.get_job(job_id)
    assert stored is not None
    assert stored["state"] == "failed"
    assert stored["finished_at"] is not None


def test_a_job_orphaned_by_a_restart_is_failed_and_called_back():
    """A restart is the other way a job dies, and the quieter one.

    `uvicorn --reload` restarts on any code edit and a container restart does
    the same. Whatever was downloading is gone, but the row still says
    'running' and there is no longer anything alive to call back — so the
    execution waits forever on work that stopped existing. The next startup is
    the only thing left that can tell it.
    """
    with callback_recorder() as recorder:
        job_id = pipeline_db.create_job("zzzzzzzzzzz", "ingest", _resume_url(recorder))
        pipeline_db.mark_running(job_id)

        jobs.reap_orphans()

        payload = _await_callback(recorder, timeout=20.0)

    assert payload["job_id"] == job_id
    assert payload["state"] == "failed"
    # The operator reads this in a Telegram alert. "failed" alone sends them to
    # a terminal to work out whether it was YouTube, ffmpeg, or a restart.
    assert "restart" in str(payload["error"]).lower()


def test_reaping_an_orphan_clears_the_bytes_it_left_behind():
    """A killed download's partial file is garbage nothing else collects.

    `_clear_partials` runs when yt-dlp reports a failure and at the start of
    the next attempt — neither of which a killed process reaches. Two killed
    jobs left 204 MB of `raw.part.*` on disk while verifying this feature, and
    the retention sweep only ever looks at videos that finished.
    """
    video_id = "yyyyyyyyyyy"
    directory = media.video_dir(video_id)
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "raw.part.f399.mp4.part").write_bytes(b"half a download")

    try:
        with callback_recorder() as recorder:
            job_id = pipeline_db.create_job(video_id, "ingest", _resume_url(recorder))
            pipeline_db.mark_running(job_id)
            jobs.reap_orphans()
            _await_callback(recorder, timeout=20.0)

        assert list(directory.glob("raw.part.*")) == []
    finally:
        shutil.rmtree(directory, ignore_errors=True)
