import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

import chunker
import library
import transcriber
from errors import (
    ChunkBoundaryError,
    EmptyTranscriptError,
    InvalidVideoIdError,
    MediaNotFoundError,
    TranscriptionError,
    TranscriptNotFoundError,
)
from schema import (
    Chunk,
    ChunkRequest,
    ChunkResponse,
    Segment,
    VideoRequest,
    VideoResponse,
)
from shared import pipeline_db

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    pipeline_db.init()
    transcriber.get_model()
    yield


app = FastAPI(lifespan=lifespan)


@app.exception_handler(InvalidVideoIdError)
async def handle_invalid_video_id(
    request: Request, exc: InvalidVideoIdError
) -> JSONResponse:
    return JSONResponse(status_code=400, content={"error": str(exc)})


@app.exception_handler(MediaNotFoundError)
async def handle_media_not_found(
    request: Request, exc: MediaNotFoundError
) -> JSONResponse:
    return JSONResponse(status_code=404, content={"error": str(exc)})


@app.exception_handler(TranscriptNotFoundError)
async def handle_transcript_not_found(
    request: Request, exc: TranscriptNotFoundError
) -> JSONResponse:
    return JSONResponse(status_code=404, content={"error": str(exc)})


@app.exception_handler(EmptyTranscriptError)
async def handle_empty_transcript(
    request: Request, exc: EmptyTranscriptError
) -> JSONResponse:
    return JSONResponse(status_code=422, content={"error": str(exc)})


@app.exception_handler(ChunkBoundaryError)
async def handle_chunk_boundary(
    request: Request, exc: ChunkBoundaryError
) -> JSONResponse:
    return JSONResponse(status_code=422, content={"error": str(exc)})


@app.exception_handler(TranscriptionError)
async def handle_transcription_error(
    request: Request, exc: TranscriptionError
) -> JSONResponse:
    return JSONResponse(status_code=502, content={"error": str(exc)})


@app.get("/health")
async def health() -> dict[str, object]:
    return {"status": "ok", "model_loaded": transcriber.is_loaded()}


@app.post("/transcribe", response_model=VideoResponse)
def transcribe_video(request: VideoRequest) -> VideoResponse:
    media = library.load(request.video_id)
    transcript, segments, language = transcriber.transcribe(media.audio_path)

    response = VideoResponse(
        video_id=media.video_id,
        title=media.title,
        language=language,
        duration_s=media.duration_s,
        transcript=transcript,
        segments=[Segment(**segment) for segment in segments],
    )

    # On disk rather than only in the reply: chunking, translation and
    # rendering all read segments, and shipping thousands of them back through
    # n8n once per stage moves megabytes to say what the volume already knows.
    library.save_transcript(media.video_id, response.model_dump())
    pipeline_db.advance_stage(media.video_id, "transcribed")
    return response


@app.post("/chunk", response_model=ChunkResponse)
def chunk_video(request: ChunkRequest) -> ChunkResponse:
    """Cut a transcribed video into publishable chunks.

    Takes a `video_id`, not segments. Chunking used to be an n8n Code node,
    which is why its three defects went unnoticed for so long: a Code node
    cannot be tested, and it cannot write `chunks` rows either. The thresholds
    stay tunable per request, which was the only real argument for keeping it
    there.
    """
    transcript = library.load_transcript(request.video_id)
    built = chunker.build_chunks(
        transcript.get("segments"),
        request.max_chunk_s,
        request.min_chunk_s,
        request.single_chunk_max_s,
        request.boundary_shift_max_s,
        transcript.get("duration_s"),
    )

    pipeline_db.replace_chunks(
        request.video_id,
        [
            {
                "idx": chunk.idx,
                "name": chunk.name,
                "start_s": chunk.start_s,
                "end_s": chunk.end_s,
                "duration_s": chunk.duration_s,
                "boundary_shift_s": chunk.boundary_shift_s,
                "text": chunk.text,
            }
            for chunk in built
        ],
    )
    pipeline_db.advance_stage(request.video_id, "chunked")

    return ChunkResponse(
        video_id=request.video_id,
        total_chunks=len(built),
        max_chunk_s=request.max_chunk_s,
        min_chunk_s=request.min_chunk_s,
        single_chunk_max_s=request.single_chunk_max_s,
        boundary_shift_max_s=request.boundary_shift_max_s,
        chunks=[Chunk(**vars(chunk)) for chunk in built],
    )
