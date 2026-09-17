from pydantic import ValidationError
import pytest

from schema import MediaPreviewJobRequest


pytestmark = pytest.mark.no_pipeline


def _request(payload: dict) -> MediaPreviewJobRequest:
    return MediaPreviewJobRequest.model_validate({
        "video_id": "jNQXAC9IVRw", "brands": {"an-so": payload},
    })


def test_preview_accepts_one_chunk():
    request = _request({"revision": "latest", "variants": "chunks", "chunks": [1]})
    assert request.brands["an-so"].chunks == [1]


def test_every_preview_variant_is_landscape_now():
    assert set(MediaPreviewJobRequest.model_fields.keys()) >= {"brands"}
    for variants in ("all", "whole", "chunks"):
        assert _request({"variants": variants}).brands["an-so"].variants == variants


@pytest.mark.parametrize("name", ["vertical", "landscape"])
def test_the_old_aspect_names_are_refused_rather_than_reinterpreted(name):
    """`landscape` used to mean "the whole" and `vertical` "the chunks".

    Both names are gone. Silently mapping them would let an old caller keep
    working while meaning something the delivery contract no longer has, so
    they must fail loudly instead.
    """
    with pytest.raises(ValidationError):
        _request({"variants": name})


@pytest.mark.parametrize("payload", [
    {"variants": "whole", "chunks": [1]},
    {"variants": "chunks", "chunks": [0]},
    {"variants": "chunks", "chunks": [1, 1]},
])
def test_preview_rejects_invalid_chunk_selection(payload):
    with pytest.raises(ValidationError):
        _request(payload)
