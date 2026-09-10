import json

import pytest

import context
from errors import MetadataError
from shared import pipeline_db


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_db, "DB_PATH", tmp_path / "pipeline.db")
    monkeypatch.setattr(context, "DATA_DIR", tmp_path)
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
                "idx": 0,
                "name": "part_1",
                "start_s": 0,
                "end_s": 300,
                "duration_s": 300,
                "boundary_shift_s": 0,
                "text": "Nội dung phần một.",
            }
        ],
    )
    directory = tmp_path / "aaaaaaaaaaa"
    directory.mkdir()
    return directory


def write_transcript(path, *, language="vi", transcript="Bản chép lời tiếng Việt."):
    path.write_text(
        json.dumps({"language": language, "transcript": transcript}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_load_uses_translated_transcript_and_stable_chunk_identity(source):
    write_transcript(source / "transcript.vi.json")

    first = context.load("aaaaaaaaaaa")
    second = context.load("aaaaaaaaaaa")

    assert first.transcript == "Bản chép lời tiếng Việt."
    assert first.chunks[0].name == "part_1"
    assert first.transcript_hash == second.transcript_hash


def test_non_vietnamese_original_requires_translation(source):
    write_transcript(source / "transcript.json", language="en", transcript="English source.")

    with pytest.raises(MetadataError) as caught:
        context.load("aaaaaaaaaaa")

    assert caught.value.code == "translation_missing"


def test_missing_chunks_block_generation(source):
    write_transcript(source / "transcript.vi.json")
    pipeline_db.replace_chunks("aaaaaaaaaaa", [])

    with pytest.raises(MetadataError) as caught:
        context.load("aaaaaaaaaaa")

    assert caught.value.code == "chunks_missing"


def test_noncontiguous_chunk_identity_blocks_generation(source):
    write_transcript(source / "transcript.vi.json")
    with pipeline_db.connect() as db:
        db.execute(
            "UPDATE chunks SET idx = 2, name = 'part_3' WHERE video_id = ? AND idx = 0",
            ("aaaaaaaaaaa",),
        )

    with pytest.raises(MetadataError) as caught:
        context.load("aaaaaaaaaaa")

    assert caught.value.code == "chunks_invalid"
