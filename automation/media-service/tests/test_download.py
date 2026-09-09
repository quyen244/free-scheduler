import shutil

from fastapi.testclient import TestClient

import media
from main import app
from shared import pipeline_db

# "Me at the zoo" — the first video ever uploaded to YouTube (2005), chosen as
# a fixture because it is about as permanent as a YouTube URL gets. yt-dlp's
# own designated test video was taken down mid-project once already.
TEST_VIDEO_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
TEST_VIDEO_ID = "jNQXAC9IVRw"


def test_download_writes_raw_video_audio_and_metadata_for_one_url():
    shutil.rmtree(media.video_dir(TEST_VIDEO_ID), ignore_errors=True)

    with TestClient(app) as client:
        response = client.post("/download", json={"url": TEST_VIDEO_URL})

    assert response.status_code == 200
    body = response.json()
    assert body["video_id"] == TEST_VIDEO_ID
    assert body["cached"] is False

    # The whole point of this service: the video itself is on disk, not just
    # its audio. The render stage has nothing to work with otherwise.
    directory = media.video_dir(TEST_VIDEO_ID)
    raw = directory / media.RAW_NAME
    audio = directory / media.AUDIO_NAME
    assert raw.is_file() and raw.stat().st_size > 0
    assert audio.is_file() and audio.stat().st_size > 0

    # Title and duration are read from yt-dlp's result and persisted, because
    # the cache-hit path never calls yt-dlp again.
    assert body["title"].strip() != ""
    assert body["title"] != TEST_VIDEO_ID
    assert body["duration_s"] > 0


def test_ingest_records_one_video_row_however_many_times_it_runs():
    shutil.rmtree(media.video_dir(TEST_VIDEO_ID), ignore_errors=True)
    with pipeline_db.connect() as db:
        db.execute("DELETE FROM videos WHERE video_id = ?", (TEST_VIDEO_ID,))

    with TestClient(app) as client:
        first = client.post("/download", json={"url": TEST_VIDEO_URL})
        # The second call is the cache hit, and it is the one that matters: the
        # common case once a video is known. A cache hit that skips the database
        # write leaves the pipeline unable to see media that is on disk.
        second = client.post("/download", json={"url": TEST_VIDEO_URL})

    assert (first.status_code, second.status_code) == (200, 200)
    assert first.json()["cached"] is False
    assert second.json()["cached"] is True

    with pipeline_db.connect() as db:
        rows = db.execute(
            "SELECT stage, title, duration_s FROM videos WHERE video_id = ?",
            (TEST_VIDEO_ID,),
        ).fetchall()

    assert len(rows) == 1
    assert rows[0]["stage"] == "ingested"
    assert rows[0]["title"] != TEST_VIDEO_ID
    assert rows[0]["duration_s"] > 0
