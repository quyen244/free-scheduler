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


def test_download_rejects_a_real_video_below_the_five_minute_minimum():
    shutil.rmtree(media.video_dir(TEST_VIDEO_ID), ignore_errors=True)

    with TestClient(app) as client:
        response = client.post("/download", json={"url": TEST_VIDEO_URL})

    assert response.status_code == 422
    body = response.json()
    assert body["error_code"] == "duration_out_of_range"
    assert body["retryable"] is False

    # The whole point of this service: the video itself is on disk, not just
    # its audio. The render stage has nothing to work with otherwise.
    directory = media.video_dir(TEST_VIDEO_ID)
    raw = directory / media.RAW_NAME
    assert raw.is_file() and raw.stat().st_size > 0
    assert not (directory / media.AUDIO_NAME).is_file()


def test_rejected_source_records_its_typed_validation_failure():
    shutil.rmtree(media.video_dir(TEST_VIDEO_ID), ignore_errors=True)
    with pipeline_db.connect() as db:
        db.execute("DELETE FROM videos WHERE video_id = ?", (TEST_VIDEO_ID,))

    with TestClient(app) as client:
        response = client.post("/download", json={"url": TEST_VIDEO_URL})

    assert response.status_code == 422

    with pipeline_db.connect() as db:
        rows = db.execute(
            "SELECT validation_status, validation_error_code FROM videos WHERE video_id = ?",
            (TEST_VIDEO_ID,),
        ).fetchall()

    assert len(rows) == 1
    assert rows[0]["validation_status"] == "failed"
    assert rows[0]["validation_error_code"] == "duration_out_of_range"
