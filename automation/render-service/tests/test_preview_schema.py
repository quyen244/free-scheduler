from pydantic import ValidationError
import pytest

from schema import MediaPreviewJobRequest


pytestmark = pytest.mark.no_pipeline


def test_preview_accepts_one_vertical_chunk():
    request = MediaPreviewJobRequest.model_validate({
        "video_id": "jNQXAC9IVRw",
        "brands": {"an-so": {"revision": "latest", "variants": "vertical", "chunks": [1]}},
    })
    assert request.brands["an-so"].chunks == [1]


@pytest.mark.parametrize("payload", [
    {"variants": "landscape", "chunks": [1]},
    {"variants": "vertical", "chunks": [0]},
    {"variants": "vertical", "chunks": [1, 1]},
])
def test_preview_rejects_invalid_chunk_selection(payload):
    with pytest.raises(ValidationError):
        MediaPreviewJobRequest.model_validate({
            "video_id": "jNQXAC9IVRw", "brands": {"an-so": payload},
        })
