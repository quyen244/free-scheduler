import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse

import jobs
import media
from errors import AudioExtractionError, DownloadError, InvalidURLError, SourcePolicyError
from schema import DownloadRequest, JobAccepted, JobRequest, MediaResponse
from shared import pipeline_db

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    pipeline_db.init()
    # Anything still marked running belongs to a process that is gone. Settle
    # it now, before this one starts accepting work of its own.
    jobs.reap_orphans()
    yield


app = FastAPI(lifespan=lifespan)


@app.exception_handler(InvalidURLError)
async def handle_invalid_url(request: Request, exc: InvalidURLError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"error": str(exc)})


@app.exception_handler(DownloadError)
async def handle_download_error(request: Request, exc: DownloadError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"error": str(exc)})


@app.exception_handler(AudioExtractionError)
async def handle_extraction_error(
    request: Request, exc: AudioExtractionError
) -> JSONResponse:
    return JSONResponse(status_code=502, content={"error": str(exc)})


@app.exception_handler(SourcePolicyError)
async def handle_source_policy(
    request: Request, exc: SourcePolicyError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"error": str(exc), "error_code": exc.code, "retryable": False},
    )


@app.get("/health")
async def health() -> dict[str, object]:
    return {"status": "ok"}


@app.post("/media/jobs", response_model=JobAccepted, status_code=202)
def create_ingest_job(request: JobRequest, background: BackgroundTasks) -> JobAccepted:
    # Validated here rather than in the worker: a malformed URL is the caller's
    # mistake, and it deserves a 400 now instead of a failed job to go and read
    # about later.
    video_id = media.extract_video_id(request.url)

    job_id = pipeline_db.create_job(video_id, "ingest", request.callback_url)
    background.add_task(
        jobs.run_ingest,
        job_id,
        request.url,
    )
    return JobAccepted(job_id=job_id, video_id=video_id, state="queued")


@app.get("/jobs/{job_id}")
def read_job(job_id: str) -> JSONResponse:
    job = pipeline_db.get_job(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": f"no job {job_id!r}"})
    return JSONResponse(content=job)


@app.post("/download", response_model=MediaResponse)
def download(request: DownloadRequest) -> MediaResponse:
    record, cached = media.get_or_download(request.url)
    directory = media.video_dir(str(record["video_id"]))

    return MediaResponse(
        video_id=str(record["video_id"]),
        title=str(record["title"]),
        duration_s=float(record["duration_s"]),  # type: ignore[arg-type]
        raw_path=str(directory / media.RAW_NAME),
        audio_path=str(directory / media.AUDIO_NAME),
        cached=cached,
        width=int(record["width"]),
        height=int(record["height"]),
        source_hash=str(record["source_hash"]),
    )
