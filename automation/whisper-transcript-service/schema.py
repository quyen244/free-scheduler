from pydantic import BaseModel, Field

import chunker


class VideoRequest(BaseModel):
    video_id: str


class Segment(BaseModel):
    start: float
    end: float
    text: str


class VideoResponse(BaseModel):
    video_id: str
    title: str
    # Detected by Whisper, not supplied. The translate stage branches on it:
    # 'vi' passes straight through, anything else gets translated first.
    language: str
    duration_s: float
    transcript: str
    segments: list[Segment]


class ChunkRequest(BaseModel):
    video_id: str
    # Overridable per request. The threshold living outside the code was the
    # one good reason chunking sat in an n8n Code node; it survives the move.
    max_chunk_s: float = Field(default=chunker.MAX_CHUNK_S, gt=0)
    min_chunk_s: float = Field(default=chunker.MIN_CHUNK_S, ge=0)


class Chunk(BaseModel):
    idx: int
    start_s: float
    end_s: float
    # Stored as well as returned: duration is what decides whether a chunk is
    # publishable, and the original script never recorded it.
    duration_s: float
    text: str
    char_count: int


class ChunkResponse(BaseModel):
    video_id: str
    total_chunks: int
    max_chunk_s: float
    min_chunk_s: float
    chunks: list[Chunk]
