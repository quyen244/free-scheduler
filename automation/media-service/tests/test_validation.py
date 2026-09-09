import hashlib

import pytest

import validation
from errors import SourcePolicyError


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


@pytest.mark.parametrize(
    "rights_status", ["owned", "licensed", "permission", "public_domain"]
)
def test_known_rights_are_accepted(rights_status):
    validation.validate_rights(rights_status)


def test_unknown_rights_are_rejected_before_download():
    with pytest.raises(SourcePolicyError) as caught:
        validation.validate_rights("unknown")
    assert caught.value.code == "rights_unknown"


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
