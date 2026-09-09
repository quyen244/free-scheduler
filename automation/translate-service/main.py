import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse

import jobs
import library
import translator
from errors import (
    InvalidVideoIdError,
    MisalignedTranslationError,
    TranscriptNotFoundError,
    TranslationError,
)
from schema import JobAccepted, JobRequest
from shared import pipeline_db

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    pipeline_db.init()
    # Anything still marked running belongs to a process that is gone. Settle
    # it now, before this one starts accepting work of its own.
    jobs.reap_orphans()
    # Not loaded here, unlike Whisper: 1.1 GB of weights would hold the
    # healthcheck down for the whole start_period on every restart, and the
    # first job pays the load either way.
    yield


app = FastAPI(lifespan=lifespan)


@app.exception_handler(InvalidVideoIdError)
async def handle_invalid_video_id(
    request: Request, exc: InvalidVideoIdError
) -> JSONResponse:
    return JSONResponse(status_code=400, content={"error": str(exc)})


@app.exception_handler(TranscriptNotFoundError)
async def handle_transcript_not_found(
    request: Request, exc: TranscriptNotFoundError
) -> JSONResponse:
    return JSONResponse(status_code=404, content={"error": str(exc)})


@app.exception_handler(MisalignedTranslationError)
async def handle_misaligned(
    request: Request, exc: MisalignedTranslationError
) -> JSONResponse:
    return JSONResponse(status_code=422, content={"error": str(exc)})


@app.exception_handler(TranslationError)
async def handle_translation_error(
    request: Request, exc: TranslationError
) -> JSONResponse:
    return JSONResponse(status_code=502, content={"error": str(exc)})


@app.get("/health")
async def health() -> dict[str, object]:
    return {"status": "ok", "model_loaded": translator.is_loaded()}


@app.post("/translate/jobs", response_model=JobAccepted, status_code=202)
def create_translate_job(
    request: JobRequest, background: BackgroundTasks
) -> JobAccepted:
    # Checked here rather than in the worker: a video that was never
    # transcribed is the caller's mistake, and it deserves a 404 now instead of
    # a failed job to go and read about later.
    library.load_transcript(request.video_id)

    job_id = pipeline_db.create_job(request.video_id, "translate", request.callback_url)
    background.add_task(jobs.run_translate, job_id, request.video_id)
    return JobAccepted(job_id=job_id, video_id=request.video_id, state="queued")


@app.get("/jobs/{job_id}")
def read_job(job_id: str) -> JSONResponse:
    job = pipeline_db.get_job(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": f"no job {job_id!r}"})
    return JSONResponse(content=job)
