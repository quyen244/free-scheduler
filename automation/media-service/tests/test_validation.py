import hashlib

import pytest
from fastapi.testclient import TestClient

import validation
from errors import InvalidURLError, SourcePolicyError
from main import app
from media import extract_video_id


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=aaaaaaaaaaa",
        "https://youtube.com/watch?feature=share&v=aaaaaaaaaaa",
        "https://m.youtube.com/watch?v=aaaaaaaaaaa&t=12",
        "https://youtu.be/aaaaaaaaaaa?si=share",
        "https://www.youtube.com/shorts/aaaaaaaaaaa",
        "https://youtube.com/embed/aaaaaaaaaaa",
        "https://youtube.com/live/aaaaaaaaaaa?feature=share",
    ],
)
def test_supported_youtube_urls_normalize_to_one_video_id(url):
    assert extract_video_id(url) == "aaaaaaaaaaa"


@pytest.mark.parametrize(
    "url",
    [
        "https://notyoutube.com/watch?v=aaaaaaaaaaa",
        "https://youtube.com.evil.example/watch?v=aaaaaaaaaaa",
        "https://youtube.com/watch?v=too-short",
        "https://youtube.com/watch",
        "ftp://youtube.com/watch?v=aaaaaaaaaaa",
        "not a url",
    ],
)
def test_malformed_or_lookalike_youtube_urls_are_rejected(url):
    with pytest.raises(InvalidURLError):
        extract_video_id(url)


@pytest.mark.parametrize("duration_s", [300.0, 1200.0])
def test_duration_boundaries_are_inclusive(duration_s):
    validation.validate_probe(
        validation.MediaProbe(duration_s=duration_s, width=1280, height=720)
    )


@pytest.mark.parametrize("duration_s", [299.999, 1200.001])
def test_out_of_range_duration_has_a_typed_failure(duration_s):
    with pytest.raises(SourcePolicyError) as caught:
        validation.validate_probe(
            validation.MediaProbe(duration_s=duration_s, width=1920, height=1080)
        )
    assert caught.value.code == "duration_out_of_range"


def test_below_720p_has_a_typed_failure():
    with pytest.raises(SourcePolicyError) as caught:
        validation.validate_probe(
            validation.MediaProbe(duration_s=600, width=1278, height=719)
        )
    assert caught.value.code == "resolution_too_low"


def test_job_endpoint_accepts_url_only(monkeypatch):
    monkeypatch.setattr("main.jobs.run_ingest", lambda *_args: None)
    with TestClient(app) as client:
        response = client.post(
            "/media/jobs",
            json={"url": "https://www.youtube.com/watch?v=aaaaaaaaaaa"},
        )

    assert response.status_code == 202
    assert response.json()["video_id"] == "aaaaaaaaaaa"


def test_sha256_is_stable(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fixture bytes")
    assert validation.sha256(source) == hashlib.sha256(b"fixture bytes").hexdigest()


def test_corrupt_media_has_a_typed_failure(tmp_path):
    source = tmp_path / "corrupt.mp4"
    source.write_bytes(b"not a video")
    with pytest.raises(SourcePolicyError) as caught:
        validation.probe(source)
    assert caught.value.code == "media_corrupt"
