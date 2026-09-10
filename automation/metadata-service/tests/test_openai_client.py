import json

import httpx
import pytest

from errors import MetadataError
from openai_client import ResponsesClient
from schemas import YouTubeMetadata


def youtube_payload() -> dict:
    return {
        "schema_version": "metadata.v1",
        "language": "vi",
        "summary": "Video giải thích một câu chuyện từ nội dung nguồn.",
        "title": "Chi tiết đáng chú ý trong câu chuyện",
        "description": "Bản tóm tắt trung thực về nội dung chính.",
        "thumbnail_text": "Điều gì đã xảy ra?",
        "hashtags": {
            "content_specific": ["#kienthuc", "#cauchuyen", "#video"],
            "discovery": ["#trending", "#xuhuong"],
        },
    }


def response_body(payload: dict | None = None) -> dict:
    return {
        "id": "resp_test_123",
        "model": "gpt-5.6-luna",
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(payload or youtube_payload(), ensure_ascii=False),
                    }
                ],
            }
        ],
        "usage": {"input_tokens": 101, "output_tokens": 55, "total_tokens": 156},
    }


def test_responses_request_uses_selected_model_and_strict_nonstored_output():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization_present"] = request.headers.get("Authorization") is not None
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=response_body())

    client = ResponsesClient(
        api_key="test-key",
        model="gpt-5.6-luna",
        transport=httpx.MockTransport(handler),
    )
    result = client.generate(
        item_key="youtube",
        instructions="Generate Vietnamese metadata.",
        input_text="A short fixture transcript.",
        output_model=YouTubeMetadata,
    )

    assert captured["authorization_present"] is True
    assert captured["body"]["model"] == "gpt-5.6-luna"
    assert captured["body"]["store"] is False
    assert captured["body"]["max_output_tokens"] == 3000
    assert captured["body"]["reasoning"] == {"effort": "none"}
    assert captured["body"]["text"]["format"]["type"] == "json_schema"
    assert captured["body"]["text"]["format"]["strict"] is True
    assert result.value.language == "vi"
    assert result.response_id == "resp_test_123"
    assert result.total_tokens == 156


@pytest.mark.parametrize("status,retryable", [(429, True), (500, True), (400, False)])
def test_http_failures_are_classified_without_persisting_provider_body(status, retryable):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(status, json={"error": {"message": "provider detail"}})
    )
    client = ResponsesClient(api_key="test-key", model="gpt-5.6-luna", transport=transport)

    with pytest.raises(MetadataError) as caught:
        client.generate(
            item_key="youtube",
            instructions="fixture",
            input_text="fixture",
            output_model=YouTubeMetadata,
        )

    assert caught.value.retryable is retryable
    assert "provider detail" not in str(caught.value)


def test_timeout_is_retryable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("fixture timeout", request=request)

    client = ResponsesClient(
        api_key="test-key",
        model="gpt-5.6-luna",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(MetadataError) as caught:
        client.generate(
            item_key="youtube",
            instructions="fixture",
            input_text="fixture",
            output_model=YouTubeMetadata,
        )

    assert caught.value.code == "openai_transport"
    assert caught.value.retryable is True


def test_schema_invalid_provider_output_is_retryable():
    invalid = youtube_payload()
    invalid["hashtags"]["discovery"] = ["#trending"]
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=response_body(invalid))
    )
    client = ResponsesClient(api_key="test-key", model="gpt-5.6-luna", transport=transport)

    with pytest.raises(MetadataError) as caught:
        client.generate(
            item_key="youtube",
            instructions="fixture",
            input_text="fixture",
            output_model=YouTubeMetadata,
        )

    assert caught.value.code == "openai_output_invalid"
    assert caught.value.retryable is True
    assert caught.value.response_id == "resp_test_123"
    assert caught.value.response_model == "gpt-5.6-luna"
    assert caught.value.total_tokens == 156


def test_non_json_provider_output_is_retryable_and_keeps_usage_provenance():
    body = response_body()
    body["output"][0]["content"][0]["text"] = "not-json"
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    client = ResponsesClient(api_key="test-key", model="gpt-5.6-luna", transport=transport)

    with pytest.raises(MetadataError) as caught:
        client.generate(
            item_key="youtube",
            instructions="fixture",
            input_text="fixture",
            output_model=YouTubeMetadata,
        )

    assert caught.value.code == "openai_output_invalid"
    assert caught.value.response_id == "resp_test_123"
    assert caught.value.total_tokens == 156
