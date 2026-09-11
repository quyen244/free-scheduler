import json

import httpx
import pytest
from fastapi.testclient import TestClient

import library
from config import settings
from main import app
from shared import pipeline_db

# "Me at the zoo" — the first video ever uploaded to YouTube (2005), chosen as
# a fixture because it is about as permanent as a YouTube URL gets.
TEST_VIDEO_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
TEST_VIDEO_ID = "jNQXAC9IVRw"

# This service no longer downloads anything, so its happy path needs the media
# service to have ingested the fixture first. Reached by container name over
# the compose network.
MEDIA_SERVICE = "http://media-service:8001"


def _ingest(url: str) -> None:
    """Make sure the fixture's audio exists, without re-running the source gate.

    This service transcribes; it does not police the source. Media-service now
    enforces the 5:00-20:00 delivery policy, which this deliberately short
    fixture fails by design, so re-downloading it here would test the wrong
    contract. An already-ingested fixture is the precondition, and when it is
    absent the test skips instead of quietly passing on stale data.
    """
    if library.load(TEST_VIDEO_ID).audio_path.is_file():
        return
    response = httpx.post(f"{MEDIA_SERVICE}/download", json={"url": url}, timeout=300)
    if response.status_code == 422:
        pytest.skip(
            f"{TEST_VIDEO_ID} is not ingested and the source policy rejects it: "
            f"{response.json().get('error_code')}"
        )
    response.raise_for_status()


def test_transcribe_reads_ingested_audio_and_reports_title_and_language():
    _ingest(TEST_VIDEO_URL)

    with TestClient(app) as client:
        response = client.post("/transcribe", json={"video_id": TEST_VIDEO_ID})

    assert response.status_code == 200
    body = response.json()
    assert body["video_id"] == TEST_VIDEO_ID
    assert body["title"] == "Me at the zoo"
    assert body["duration_s"] > 0
    # Drives the translate branch: 'vi' skips it, anything else does not.
    assert body["language"] != ""
    assert body["transcript"].strip() != ""
    assert len(body["segments"]) > 0
    for segment in body["segments"]:
        assert segment["start"] < segment["end"]
        assert segment["text"].strip() != ""


def test_transcribe_reports_missing_media_instead_of_downloading_it():
    # The architectural assertion of this service: exactly one component talks
    # to YouTube. An id that was never ingested must fail loudly rather than
    # quietly fetching it and making that two.
    with TestClient(app) as client:
        response = client.post("/transcribe", json={"video_id": "aaaaaaaaaaa"})

    assert response.status_code == 404
    assert "media service" in response.json()["error"]


def test_transcribe_rejects_a_video_id_that_is_not_the_right_shape():
    with TestClient(app) as client:
        response = client.post("/transcribe", json={"video_id": "../../etc/passwd"})

    assert response.status_code == 400


def test_transcribe_persists_the_transcript_and_advances_the_stage():
    """The transcript is the input to three later stages, so it lands on disk.

    Chunking, translation and rendering all read segments. Handing thousands of
    them back through n8n once per stage moves megabytes of JSON around to say
    something the shared volume already knows.
    """
    _ingest(TEST_VIDEO_URL)

    with TestClient(app) as client:
        response = client.post("/transcribe", json={"video_id": TEST_VIDEO_ID})
    assert response.status_code == 200

    stored = json.loads(
        (settings.data_dir / TEST_VIDEO_ID / "transcript.json").read_text(encoding="utf-8")
    )
    assert stored["video_id"] == TEST_VIDEO_ID
    assert stored["language"] == response.json()["language"]
    assert stored["segments"] == response.json()["segments"]

    with pipeline_db.connect() as db:
        row = db.execute(
            "SELECT stage FROM videos WHERE video_id = ?", (TEST_VIDEO_ID,)
        ).fetchone()
    assert row is not None
    # At `transcribed` **or past it**. This asserted equality until the later
    # stages existed, and then failed whenever this fixture had already been
    # voiced or rendered — which is the forward-only stage rule working, not a
    # regression. What transcribing promises is that it never leaves a video
    # behind `transcribed` and never drags one backwards.
    order = pipeline_db.STAGE_ORDER
    assert order.index(row["stage"]) >= order.index("transcribed")
