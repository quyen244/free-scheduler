import json
from pathlib import Path

import query_metadata
from shared import pipeline_db


VIDEO_ID = "aaaaaaaaaaa"


def _selected_youtube() -> dict:
    return {
        "schema_version": "metadata.v1",
        "language": "vi",
        "summary": "Tom tat video.",
        "title": "Tieu de YouTube",
        "description": "Mo ta YouTube.",
        "thumbnail_text": "Chu thumbnail",
        "hashtags": {
            "content_specific": ["#mot", "#hai", "#ba"],
            "discovery": ["#bon", "#nam"],
        },
    }


def _selected_chunk(part: str) -> dict:
    return {
        "schema_version": "metadata.v1",
        "language": "vi",
        "chunk_name": part,
        "hook": f"Hook {part}",
        "visual_caption": f"Visual caption {part}",
        "facebook": {
            "caption": f"Facebook caption {part}",
            "hashtags": {
                "content_specific": ["#mot", "#hai", "#ba"],
                "discovery": ["#bon", "#nam"],
            },
        },
        "tiktok": {
            "caption": f"TikTok caption {part}",
            "hashtags": {
                "content_specific": ["#sau", "#bay", "#tam"],
                "discovery": ["#chin", "#muoi"],
            },
        },
    }


def _seed_selected_metadata(monkeypatch, tmp_path):
    db_path = tmp_path / "pipeline.db"
    monkeypatch.setattr(pipeline_db, "DB_PATH", db_path)
    monkeypatch.setattr(pipeline_db, "SCHEMA_PATH", tmp_path / "schema.sql")
    (tmp_path / "schema.sql").write_text(
        (Path(__file__).resolve().parents[2] / "data" / "schema.sql").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    pipeline_db.init()
    pipeline_db.record_ingested(VIDEO_ID, "https://example.test/video", "Nguon", 600)
    pipeline_db.replace_chunks(
        VIDEO_ID,
        [
            {
                "idx": 0,
                "name": "part_1",
                "start_s": 0.0,
                "end_s": 300.0,
                "duration_s": 300.0,
                "boundary_shift_s": 0.0,
                "text": "Noi dung 1",
            },
            {
                "idx": 1,
                "name": "part_2",
                "start_s": 300.0,
                "end_s": 600.0,
                "duration_s": 300.0,
                "boundary_shift_s": 0.0,
                "text": "Noi dung 2",
            },
        ],
    )
    revision_id = "b" * 32
    with pipeline_db.connect() as db:
        db.execute(
            """
            INSERT INTO metadata_revisions (
                revision_id, video_id, revision_number, generation_key,
                transcript_hash, prompt_version, schema_version, model, state
            ) VALUES (?, ?, 1, 'key', 'hash', 'metadata.vi.v2', 'metadata.v1', 'test', 'selected')
            """,
            (revision_id, VIDEO_ID),
        )
        db.executemany(
            """
            INSERT INTO metadata_items (
                revision_id, item_key, kind, chunk_idx, state, selected_json
            ) VALUES (?, ?, ?, ?, 'selected', ?)
            """,
            [
                (revision_id, "youtube", "youtube", None, json.dumps(_selected_youtube())),
                (revision_id, "part_1", "chunk", 0, json.dumps(_selected_chunk("part_1"))),
                (revision_id, "part_2", "chunk", 1, json.dumps(_selected_chunk("part_2"))),
            ],
        )
    return db_path


def test_selected_metadata_returns_only_platform_posting_fields(monkeypatch, tmp_path):
    db_path = _seed_selected_metadata(monkeypatch, tmp_path)

    result = query_metadata.selected_metadata(db_path, VIDEO_ID)

    assert result["video"]["video_id"] == VIDEO_ID
    assert result["metadata_revision"]["revision_number"] == 1
    assert result["youtube"]["title"] == "Tieu de YouTube"
    assert result["chunks"][0] == {
        "part": "part_1",
        "index": 1,
        "start_s": 0.0,
        "end_s": 300.0,
        "duration_s": 300.0,
        "hook": "Hook part_1",
        "visual_caption": "Visual caption part_1",
        "facebook": _selected_chunk("part_1")["facebook"],
        "tiktok": _selected_chunk("part_1")["tiktok"],
    }


def test_cli_emits_json_and_returns_a_clear_error_for_unknown_video(monkeypatch, tmp_path, capsys):
    db_path = _seed_selected_metadata(monkeypatch, tmp_path)

    assert query_metadata.main(["--id", VIDEO_ID, "--db", str(db_path)]) == 0
    assert json.loads(capsys.readouterr().out)["chunks"][1]["part"] == "part_2"

    assert query_metadata.main(["--id", "missing", "--db", str(db_path)]) == 2
    assert "video was not found: missing" in capsys.readouterr().err
