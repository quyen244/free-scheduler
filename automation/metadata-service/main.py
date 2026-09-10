import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import BackgroundTasks, FastAPI, HTTPException

import jobs
import repository
from schemas import ChunkMetadata, MetadataJobAccepted, MetadataJobRequest, YouTubeMetadata
from shared import pipeline_db


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    pipeline_db.init()
    jobs.reap_orphans()
    yield


app = FastAPI(title="Re-up metadata service", version="0.2.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/schemas/youtube")
def youtube_schema() -> dict:
    return YouTubeMetadata.model_json_schema()


@app.get("/schemas/chunk")
def chunk_schema() -> dict:
    return ChunkMetadata.model_json_schema()


@app.post("/metadata/jobs", response_model=MetadataJobAccepted, status_code=202)
def create_metadata_job(
    request: MetadataJobRequest, background: BackgroundTasks
) -> MetadataJobAccepted:
    model = os.environ.get("OPENAI_MODEL", "").strip()
    if not model:
        raise HTTPException(
            status_code=503,
            detail="OPENAI_MODEL is not configured for the metadata service.",
        )
    job_id, state, reused = pipeline_db.create_or_reuse_active_job(
        request.video_id, "metadata", request.callback_url
    )
    if not reused:
        background.add_task(jobs.run, job_id, request.video_id, model)
    return MetadataJobAccepted(
        job_id=job_id,
        video_id=request.video_id,
        state=state,
        reused=reused,
    )


@app.get("/jobs/{job_id}")
def read_job(job_id: str) -> dict:
    job = pipeline_db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Metadata job not found.")
    return job


@app.get("/metadata/revisions/{revision_id}")
def read_metadata_revision(revision_id: str) -> dict:
    try:
        return repository.revision_bundle(revision_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Metadata revision not found.") from exc
