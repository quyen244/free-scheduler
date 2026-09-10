import pytest
from pydantic import ValidationError

from schemas import ChunkMetadata, HashtagSet, YouTubeMetadata


def hashtags() -> dict[str, list[str]]:
    return {
        "content_specific": ["#kienthuc", "#cauchuyen", "#video"],
        "discovery": ["#trending", "#xuhuong"],
    }


def youtube_payload() -> dict:
    return {
        "schema_version": "metadata.v1",
        "language": "vi",
        "summary": "Video giải thích một câu chuyện dựa trên nội dung nguồn.",
        "title": "Điều đáng chú ý trong câu chuyện này",
        "description": "Tóm tắt trung thực những nội dung chính của video.",
        "thumbnail_text": "Có gì đáng chú ý?",
        "hashtags": hashtags(),
    }


def chunk_payload() -> dict:
    return {
        "schema_version": "metadata.v1",
        "language": "vi",
        "chunk_name": "part_1",
        "hook": "Chi tiết nào đã thay đổi câu chuyện?",
        "visual_caption": "Phần một trình bày bối cảnh chính.",
        "facebook": {
            "caption": "Bạn chú ý điều gì trong phần này?",
            "hashtags": hashtags(),
        },
        "tiktok": {
            "caption": "Xem phần một để hiểu bối cảnh của câu chuyện.",
            "hashtags": hashtags(),
        },
    }


def test_youtube_schema_accepts_one_complete_vietnamese_result():
    result = YouTubeMetadata.model_validate(youtube_payload())
    assert result.hashtags.flattened() == [
        "#kienthuc",
        "#cauchuyen",
        "#video",
        "#trending",
        "#xuhuong",
    ]


def test_chunk_schema_keeps_facebook_and_tiktok_copy_separate():
    result = ChunkMetadata.model_validate(chunk_payload())
    assert result.chunk_name == "part_1"
    assert result.facebook.caption != result.tiktok.caption


@pytest.mark.parametrize(
    "field,value",
    [
        ("content_specific", ["#one", "#two"]),
        ("discovery", ["#one", "#two", "#three"]),
        ("content_specific", ["#one", "bad tag", "#three"]),
    ],
)
def test_hashtag_groups_reject_wrong_counts_and_invalid_format(field, value):
    payload = hashtags()
    payload[field] = value
    with pytest.raises(ValidationError):
        HashtagSet.model_validate(payload)


def test_hashtags_must_be_unique_across_both_groups():
    payload = hashtags()
    payload["discovery"][0] = "#VIDEO"
    with pytest.raises(ValidationError):
        HashtagSet.model_validate(payload)


def test_chunk_identity_must_use_part_number_format():
    payload = chunk_payload()
    payload["chunk_name"] = "chunk-one"
    with pytest.raises(ValidationError):
        ChunkMetadata.model_validate(payload)


def test_more_than_two_emojis_are_rejected_per_platform_object():
    payload = chunk_payload()
    payload["facebook"]["caption"] = "Một 😀 hai 😀 ba 😀"
    with pytest.raises(ValidationError):
        ChunkMetadata.model_validate(payload)


def test_unknown_fields_are_rejected():
    payload = youtube_payload()
    payload["unreviewed_claim"] = "not allowed"
    with pytest.raises(ValidationError):
        YouTubeMetadata.model_validate(payload)


def test_json_schema_is_strict_and_versioned():
    schema = ChunkMetadata.model_json_schema()
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"]["const"] == "metadata.v1"
    assert set(schema["required"]) == set(schema["properties"])
