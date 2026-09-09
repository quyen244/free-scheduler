"""F4 — chunking.

Two halves. The first exercises `chunker.build_chunks` directly on synthetic
segments, because the interesting cases (a 5-second tail, a segment longer than
the ceiling, an empty transcript) are ones no real video conveniently has. The
second drives `POST /chunk` end to end against a real transcript and checks
what landed in the database.
"""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

import chunker
from config import settings
from errors import ChunkBoundaryError, EmptyTranscriptError
from main import app
from shared import pipeline_db

# An 11-minute source: long enough to produce several chunks, so the boundary
# rules are actually exercised. "Me at the zoo" is 19 s and yields exactly one.
TEST_VIDEO_URL = "https://www.youtube.com/watch?v=3gi_15UH9fQ"
TEST_VIDEO_ID = "3gi_15UH9fQ"

MEDIA_SERVICE = "http://media-service:8001"

MAX_CHUNK_S = 300.0
MIN_CHUNK_S = 240.0


def _segments(*spans: tuple[float, float]) -> list[dict[str, object]]:
    return [
        {"start": start, "end": end, "text": f"seg{i}"}
        for i, (start, end) in enumerate(spans)
    ]


def _evenly(count: int, length: float) -> list[dict[str, object]]:
    return _segments(*[(i * length, (i + 1) * length) for i in range(count)])


# --- the algorithm --------------------------------------------------------


def test_chunk_boundaries_fall_on_segment_edges_and_respect_the_ceiling():
    segments = _evenly(120, 5.0)  # 600 s of 5-second segments

    chunks = chunker.build_chunks(segments, MAX_CHUNK_S, MIN_CHUNK_S)

    edges = {segment["start"] for segment in segments}
    edges.add(segments[-1]["end"])
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.start_s in edges
        assert chunk.end_s in edges
        assert chunk.duration_s <= MAX_CHUNK_S

    # Nothing dropped and nothing overlapping: the chunks tile the transcript.
    assert chunks[0].start_s == segments[0]["start"]
    assert chunks[-1].end_s == segments[-1]["end"]
    for earlier, later in zip(chunks, chunks[1:]):
        assert earlier.end_s == later.start_s


@pytest.mark.parametrize(
    ("duration_s", "expected_durations"),
    [
        (300, [300]),
        (540, [540]),
        (600, [300, 300]),
        (822, [274, 274, 274]),
        (1200, [300, 300, 300, 300]),
    ],
)
def test_reference_contract_chunk_counts(duration_s, expected_durations):
    chunks = chunker.build_chunks(_evenly(duration_s, 1.0))

    assert [chunk.duration_s for chunk in chunks] == expected_durations
    assert [chunk.name for chunk in chunks] == [
        f"part_{index}" for index in range(1, len(chunks) + 1)
    ]


def test_every_chunk_records_its_own_duration():
    # The field the original script never wrote, and the one that decides
    # whether a chunk is publishable.
    chunks = chunker.build_chunks(_evenly(120, 5.0), MAX_CHUNK_S, MIN_CHUNK_S)

    for chunk in chunks:
        assert chunk.duration_s == pytest.approx(chunk.end_s - chunk.start_s)


def test_video_duration_not_last_spoken_word_controls_complete_coverage():
    segments = _evenly(59, 10.0)  # speech ends at 9:50 in a 10:00 video

    chunks = chunker.build_chunks(segments, source_duration_s=600.0)

    assert len(chunks) == 2
    assert chunks[0].start_s == 0.0
    assert chunks[-1].end_s == 600.0


def test_a_five_minute_source_is_one_chunk_not_a_greedy_tail():
    chunks = chunker.build_chunks(_evenly(60, 5.0), MAX_CHUNK_S, MIN_CHUNK_S)

    assert len(chunks) == 1
    assert chunks[0].duration_s == 300.0


def test_a_video_shorter_than_the_floor_is_still_one_whole_chunk():
    # The floor removes unusable *tails*. It must not delete a short video.
    chunks = chunker.build_chunks(_evenly(4, 5.0), MAX_CHUNK_S, MIN_CHUNK_S)

    assert len(chunks) == 1
    assert chunks[0].start_s == 0.0
    assert chunks[0].end_s == 20.0


def test_a_missing_or_empty_segment_list_is_rejected_loudly():
    # The original read `$input.first().json.segments` with no guard, so an
    # upstream failure became an unrelated crash inside the Code node.
    with pytest.raises(EmptyTranscriptError):
        chunker.build_chunks([], MAX_CHUNK_S, MIN_CHUNK_S)
    with pytest.raises(EmptyTranscriptError):
        chunker.build_chunks(None, MAX_CHUNK_S, MIN_CHUNK_S)


def test_a_missing_safe_boundary_is_rejected_instead_of_cutting_speech():
    with pytest.raises(ChunkBoundaryError):
        chunker.build_chunks(
            _segments((0.0, 500.0), (500.0, 600.0)),
            MAX_CHUNK_S,
            MIN_CHUNK_S,
            single_chunk_max_s=0,
        )


def test_text_is_joined_trimmed_and_counted():
    segments = [
        {"start": 0.0, "end": 1.0, "text": " one "},
        {"start": 1.0, "end": 2.0, "text": "two"},
    ]

    chunk = chunker.build_chunks(segments, MAX_CHUNK_S, MIN_CHUNK_S)[0]

    assert chunk.text == "one two"
    # The original counted the untrimmed buffer, so char_count never matched
    # the text it was reported alongside.
    assert chunk.char_count == len(chunk.text)


# --- the endpoint ---------------------------------------------------------


def _transcribe() -> None:
    httpx.post(
        f"{MEDIA_SERVICE}/download", json={"url": TEST_VIDEO_URL}, timeout=600
    ).raise_for_status()
    with TestClient(app) as client:
        client.post("/transcribe", json={"video_id": TEST_VIDEO_ID}).raise_for_status()


def test_chunk_writes_rows_and_advances_the_stage():
    _transcribe()

    with TestClient(app) as client:
        response = client.post("/chunk", json={"video_id": TEST_VIDEO_ID})

    assert response.status_code == 200
    body = response.json()
    assert body["video_id"] == TEST_VIDEO_ID
    assert body["total_chunks"] == len(body["chunks"])
    assert body["total_chunks"] > 1

    with pipeline_db.connect() as db:
        rows = db.execute(
            "SELECT * FROM chunks WHERE video_id = ? ORDER BY idx", (TEST_VIDEO_ID,)
        ).fetchall()
        stage = db.execute(
            "SELECT stage FROM videos WHERE video_id = ?", (TEST_VIDEO_ID,)
        ).fetchone()["stage"]

    assert len(rows) == body["total_chunks"]
    assert [row["idx"] for row in rows] == list(range(len(rows)))
    for row in rows:
        assert row["name"] == f"part_{row['idx'] + 1}"
        assert abs(row["boundary_shift_s"]) <= chunker.BOUNDARY_SHIFT_MAX_S
        assert row["duration_s"] == pytest.approx(row["end_s"] - row["start_s"])
        assert row["duration_s"] > 0
        assert row["text"].strip() != ""
        # Not `== "pending"`. `replace_chunks` keeps the render state of a chunk
        # whose boundaries did not move, so re-chunking a fixture that has
        # already been rendered legitimately returns rows past `pending` — the
        # same forward-only rule the stage assertion below documents. What must
        # hold is that a row never claims a render without naming the file.
        assert (row["status"] == "pending") == (row["final_path"] is None)

    # At `chunked` **or past it**. This asserted equality until F7 rendered the
    # fixture, and then failed on a video that had gone further — which is the
    # forward-only stage rule working, not a regression. What chunking promises
    # is that it never leaves a video behind `chunked` and never drags one
    # backwards, and that is what is checked.
    order = pipeline_db.STAGE_ORDER
    assert order.index(stage) >= order.index("chunked")


def test_chunking_twice_leaves_one_set_of_rows():
    _transcribe()

    with TestClient(app) as client:
        first = client.post("/chunk", json={"video_id": TEST_VIDEO_ID}).json()
        # A second call with a smaller ceiling produces more chunks. The rows
        # left behind must describe the second run, not both runs interleaved.
        second = client.post(
            "/chunk",
            json={
                "video_id": TEST_VIDEO_ID,
                "max_chunk_s": 120.0,
                "min_chunk_s": 60.0,
                "single_chunk_max_s": 0.0,
            },
        ).json()

    assert second["total_chunks"] > first["total_chunks"]

    with pipeline_db.connect() as db:
        rows = db.execute(
            "SELECT idx, start_s, end_s FROM chunks WHERE video_id = ? ORDER BY idx",
            (TEST_VIDEO_ID,),
        ).fetchall()

    assert len(rows) == second["total_chunks"]
    assert [row["idx"] for row in rows] == list(range(len(rows)))
    assert [row["start_s"] for row in rows] == [c["start_s"] for c in second["chunks"]]

    # Back to the default, so the fixture is left the way the other tests expect.
    with TestClient(app) as client:
        client.post("/chunk", json={"video_id": TEST_VIDEO_ID})


def test_chunk_refuses_a_video_that_was_never_transcribed():
    with TestClient(app) as client:
        response = client.post("/chunk", json={"video_id": "aaaaaaaaaaa"})

    assert response.status_code == 404
    assert "transcript" in response.json()["error"]


def test_chunk_rejects_a_video_id_that_is_not_the_right_shape():
    with TestClient(app) as client:
        response = client.post("/chunk", json={"video_id": "../../etc/passwd"})

    assert response.status_code == 400


def test_chunking_prefers_the_translated_transcript(tmp_path):
    """Chunk text becomes the caption, the subtitles and the TTS script.

    Chunking the English transcript of a video that has already been translated
    would ship an English video with Vietnamese metadata, and nothing
    downstream would notice.
    """
    _transcribe()
    directory = settings.data_dir / TEST_VIDEO_ID
    original = json.loads((directory / "transcript.json").read_text(encoding="utf-8"))
    translated_path = directory / "transcript.vi.json"
    existing = translated_path.read_text(encoding="utf-8") if translated_path.is_file() else None

    marked = {
        **original,
        "language": "vi",
        "source_language": original["language"],
        "segments": [
            {**segment, "text": f"VI {segment['text']}"}
            for segment in original["segments"]
        ],
    }
    translated_path.write_text(
        json.dumps(marked, ensure_ascii=False), encoding="utf-8"
    )
    try:
        with TestClient(app) as client:
            body = client.post("/chunk", json={"video_id": TEST_VIDEO_ID}).json()
        assert body["chunks"][0]["text"].startswith("VI ")
        # Timings are copied by the translator, so the boundaries must not move.
        with TestClient(app) as client:
            translated_path.unlink()
            plain = client.post("/chunk", json={"video_id": TEST_VIDEO_ID}).json()
        assert [c["start_s"] for c in body["chunks"]] == [
            c["start_s"] for c in plain["chunks"]
        ]
    finally:
        if existing is None:
            translated_path.unlink(missing_ok=True)
        else:
            translated_path.write_text(existing, encoding="utf-8")
