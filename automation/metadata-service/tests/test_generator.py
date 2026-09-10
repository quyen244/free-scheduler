from dataclasses import replace

import pytest

import generator
import repository
from context import ChunkContext, GenerationContext
from errors import MetadataError
from openai_client import Generated
from schemas import ChunkMetadata, YouTubeMetadata
from shared import pipeline_db


def hashtag_payload() -> dict:
    return {
        "content_specific": ["#kienthuc", "#cauchuyen", "#video"],
        "discovery": ["#trending", "#xuhuong"],
    }


def youtube_result() -> YouTubeMetadata:
    return YouTubeMetadata.model_validate(
        {
            "schema_version": "metadata.v1",
            "language": "vi",
            "summary": "Video kể lại một sự việc và các chi tiết chính.",
            "title": "Chi tiết đáng chú ý trong câu chuyện",
            "description": "Tóm tắt trung thực từ nội dung nguồn.",
            "thumbnail_text": "Điều gì đã xảy ra?",
            "hashtags": hashtag_payload(),
        }
    )


def chunk_result(name: str) -> ChunkMetadata:
    return ChunkMetadata.model_validate(
        {
            "schema_version": "metadata.v1",
            "language": "vi",
            "chunk_name": name,
            "hook": "Chi tiết nào làm câu chuyện thay đổi?",
            "visual_caption": "Phần này trình bày diễn biến chính.",
            "facebook": {
                "caption": "Bạn chú ý điều gì trong phần này?",
                "hashtags": hashtag_payload(),
            },
            "tiktok": {
                "caption": "Xem phần này để hiểu diễn biến của câu chuyện.",
                "hashtags": hashtag_payload(),
            },
        }
    )


class FakeClient:
    model = "gpt-5.6-luna"

    def __init__(self, failures=None):
        self.failures = dict(failures or {})
        self.calls = []

    def generate(self, *, item_key, instructions, input_text, output_model):
        self.calls.append(item_key)
        failure = self.failures.get(item_key)
        if failure:
            remaining, retryable = failure
            if remaining > 0:
                self.failures[item_key] = (remaining - 1, retryable)
                raise MetadataError("fixture_failure", "fixture failure", retryable=retryable)
        value = youtube_result() if output_model is YouTubeMetadata else chunk_result(item_key)
        return Generated(
            value=value,
            response_id=f"resp_{item_key}_{len(self.calls)}",
            model=self.model,
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
        )


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_db, "DB_PATH", tmp_path / "pipeline.db")
    pipeline_db.init()
    pipeline_db.record_ingested(
        "aaaaaaaaaaa",
        "https://youtu.be/aaaaaaaaaaa",
        "Fixture video",
        600,
        source_hash="sha256-fixture",
        width=1920,
        height=1080,
    )
    pipeline_db.replace_chunks(
        "aaaaaaaaaaa",
        [
            {
                "idx": index,
                "name": f"part_{index + 1}",
                "start_s": index * 300,
                "end_s": (index + 1) * 300,
                "duration_s": 300,
                "boundary_shift_s": 0,
                "text": f"Nội dung phần {index + 1}.",
            }
            for index in range(4)
        ],
    )
    yield


def fixture_context(transcript_hash="hash-v1") -> GenerationContext:
    return GenerationContext(
        video_id="aaaaaaaaaaa",
        title="Fixture video",
        transcript="Đây là toàn bộ bản chép lời dùng cho kiểm thử.",
        transcript_hash=transcript_hash,
        chunks=(
            ChunkContext(0, "part_1", "Nội dung phần một."),
            ChunkContext(1, "part_2", "Nội dung phần hai."),
        ),
    )


def test_selective_retry_persists_provenance_and_reuses_selected_results(isolated_db):
    client = FakeClient({"part_1": (1, True)})
    result = generator.run(fixture_context(), client, sleep=lambda seconds: None)

    assert result["state"] == "selected"
    assert client.calls == ["youtube", "part_1", "part_1", "part_2"]
    attempts = repository.attempts_for(result["revision_id"])
    assert len(attempts) == 4
    assert [row["state"] for row in attempts].count("failed") == 1
    assert all(row["model"] == "gpt-5.6-luna" for row in attempts)
    assert all(
        row["response_model"] == "gpt-5.6-luna"
        for row in attempts
        if row["state"] == "selected"
    )
    assert sum(row["total_tokens"] or 0 for row in attempts) == 450
    assert repository.selected_item(result["revision_id"], "part_1")["chunk_name"] == "part_1"
    rendered_inputs = pipeline_db.chunks_for("aaaaaaaaaaa")
    assert rendered_inputs[0]["hook"] == "Chi tiết nào làm câu chuyện thay đổi?"
    assert rendered_inputs[0]["caption"] == "Phần này trình bày diễn biến chính."
    bundle = repository.revision_bundle(result["revision_id"])
    assert bundle["items"][0]["item_key"] == "youtube"
    assert bundle["items"][1]["selected"]["chunk_name"] == "part_1"
    assert bundle["items"][1]["response_model"] == "gpt-5.6-luna"
    assert len(bundle["attempts"]) == 4
    assert bundle["usage"]["total_tokens"] == 450

    cached_client = FakeClient()
    cached = generator.run(fixture_context(), cached_client, sleep=lambda seconds: None)
    assert cached["revision_id"] == result["revision_id"]
    assert cached_client.calls == []


def test_invalid_paid_response_usage_is_kept_without_storing_rejected_output(isolated_db):
    class InvalidThenValid(FakeClient):
        def generate(self, **kwargs):
            if not self.calls:
                self.calls.append(kwargs["item_key"])
                raise MetadataError(
                    "openai_output_invalid",
                    "OpenAI returned metadata that failed validation.",
                    retryable=True,
                    response_id="resp_invalid_fixture",
                    response_model="gpt-5.6-luna-2026-09-01",
                    input_tokens=101,
                    output_tokens=55,
                    total_tokens=156,
                )
            return super().generate(**kwargs)

    result = generator.run(fixture_context(), InvalidThenValid(), sleep=lambda seconds: None)
    attempts = repository.attempts_for(result["revision_id"])
    failed = next(row for row in attempts if row["state"] == "failed")
    bundle = repository.revision_bundle(result["revision_id"])

    assert failed["response_id"] == "resp_invalid_fixture"
    assert failed["response_model"] == "gpt-5.6-luna-2026-09-01"
    assert failed["total_tokens"] == 156
    assert "selected_json" not in failed
    assert bundle["usage"]["total_tokens"] == 606


def test_transcript_change_creates_a_new_revision(isolated_db):
    first = generator.run(fixture_context("hash-v1"), FakeClient(), sleep=lambda seconds: None)
    second_client = FakeClient()
    second = generator.run(
        replace(fixture_context("hash-v1"), transcript_hash="hash-v2"),
        second_client,
        sleep=lambda seconds: None,
    )

    assert second["revision_id"] != first["revision_id"]
    assert second_client.calls == ["youtube", "part_1", "part_2"]
    assert repository.get_revision(first["revision_id"])["state"] == "stale"


def test_nonretryable_chunk_failure_preserves_peers_for_operator_retry(isolated_db):
    failed_client = FakeClient({"part_1": (1, False)})
    failed = generator.run(fixture_context(), failed_client, sleep=lambda seconds: None)

    assert failed["state"] == "needs_action"
    assert repository.selected_item(failed["revision_id"], "youtube") is not None
    assert repository.selected_item(failed["revision_id"], "part_2") is not None
    assert repository.selected_item(failed["revision_id"], "part_1") is None

    retry_client = FakeClient()
    recovered = generator.run(fixture_context(), retry_client, sleep=lambda seconds: None)
    assert recovered["state"] == "selected"
    assert retry_client.calls == ["part_1"]


def test_restart_closes_running_attempt_and_makes_item_retryable(isolated_db):
    revision = repository.ensure_revision(
        fixture_context(), generator.PROMPT_VERSION, "gpt-5.6-luna"
    )
    attempt_id, _ = repository.start_attempt(
        revision["revision_id"], "youtube", "gpt-5.6-luna"
    )

    repository.abandon_running_attempts("aaaaaaaaaaa")

    attempts = repository.attempts_for(revision["revision_id"])
    assert attempts[0]["attempt_id"] == attempt_id
    assert attempts[0]["state"] == "failed"
    assert attempts[0]["error_code"] == "service_restarted"
    recovered = generator.run(fixture_context(), FakeClient(), sleep=lambda seconds: None)
    assert recovered["state"] == "selected"


@pytest.mark.parametrize("chunk_count", [1, 3, 4])
def test_variable_chunk_count_produces_one_youtube_result_and_every_chunk(
    isolated_db, chunk_count
):
    context = GenerationContext(
        video_id="aaaaaaaaaaa",
        title="Fixture video",
        transcript="Bản chép lời đầy đủ cho số lượng phần thay đổi.",
        transcript_hash=f"hash-{chunk_count}",
        chunks=tuple(
            ChunkContext(index, f"part_{index + 1}", f"Nội dung phần {index + 1}.")
            for index in range(chunk_count)
        ),
    )
    client = FakeClient()

    result = generator.run(context, client, sleep=lambda seconds: None)

    assert result["state"] == "selected"
    assert client.calls == ["youtube"] + [f"part_{index + 1}" for index in range(chunk_count)]
    assert len(result["items"]) == chunk_count + 1
