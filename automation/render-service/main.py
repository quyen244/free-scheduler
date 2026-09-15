import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse

import jobs
import matting
import matting_jobs
import brand
import library
import render
import visual_preset
import voice
from config import settings
from errors import (
    BrandConfigError,
    EmptyTranscriptError,
    InvalidVideoIdError,
    NoChunksError,
    PresetNotFoundError,
    RenderError,
    SynthesisError,
    TranscriptNotFoundError,
    UnknownVoiceError,
    VoiceTrackNotFoundError,
)
from schema import (
    JobAccepted,
    MediaRevisionJobRequest,
    RenderJobRequest,
    PresetMattingRequest,
    VoiceJobRequest,
)
from shared import pipeline_db

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    pipeline_db.init()
    # Anything still marked running belongs to a process that is gone. Settle
    # it now, before this one starts accepting work of its own.
    jobs.reap_orphans()
    # The 900 MB ONNX graph is not loaded here: it would hold the healthcheck
    # down for half a minute on every restart, and the first job pays the load
    # either way.
    yield


app = FastAPI(lifespan=lifespan)


def _handler(status: int):
    async def handle(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=status, content={"error": str(exc)})

    return handle


for error, status in (
    (InvalidVideoIdError, 400),
    (UnknownVoiceError, 400),
    (TranscriptNotFoundError, 404),
    (VoiceTrackNotFoundError, 404),
    (PresetNotFoundError, 404),
    (NoChunksError, 409),
    (EmptyTranscriptError, 422),
    (SynthesisError, 502),
    (RenderError, 502),
    (BrandConfigError, 422),
):
    app.add_exception_handler(error, _handler(status))


@app.get("/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "model_loaded": voice.is_loaded(),
        # Surfaced on /health so an operator can see a CPU fallback
        # before starting a render, not after waiting hours for one.
        "encoding": render.encoder_choice().as_dict(),
    }


@app.get("/voices")
async def list_voices() -> dict[str, object]:
    """The voice enum, without loading the model.

    ZeroTTS 0.1.2 still cannot build a voice from a reference clip — the voice
    encoder is unpublished — so this list is the whole choice available.
    """
    return {"voices": list(voice.SHIPPED_VOICES), "default": settings.voice}


@app.get("/preset-editor/rvm")
def rvm_status() -> dict[str, object]:
    return matting.model_status()


@app.get("/preset-editor/presets")
def list_editor_presets() -> dict[str, object]:
    return {"presets": visual_preset.list_presets()}


@app.get("/preset-editor/presets/{preset_id}/draft")
def get_editor_draft(preset_id: str) -> dict[str, object]:
    return visual_preset.load_draft(preset_id).model_dump(mode="json", exclude_none=True)


@app.post("/preset-editor/drafts")
def save_editor_draft(payload: dict[str, object]) -> dict[str, object]:
    return visual_preset.save_draft(payload).model_dump(mode="json", exclude_none=True)


@app.post("/preset-editor/publish")
def publish_editor_preset(payload: dict[str, object]) -> dict[str, object]:
    return visual_preset.publish(payload).model_dump(mode="json", exclude_none=True)


@app.get("/preset-editor/presets/{preset_id}/revisions/{revision}")
def get_published_editor_preset(preset_id: str, revision: int) -> dict[str, object]:
    return visual_preset.load_published(preset_id, revision).model_dump(mode="json", exclude_none=True)


@app.post("/preset-editor/matting", status_code=202)
def create_matting_job(request: PresetMattingRequest, background: BackgroundTasks) -> dict[str, object]:
    accepted = matting_jobs.create(request.preset_id, request.host_asset, request.corrections)
    background.add_task(matting_jobs.run, str(accepted["job_id"]))
    return accepted


@app.get("/preset-editor/matting/{job_id}")
def get_matting_job(job_id: str) -> dict[str, object]:
    return matting_jobs.read(job_id)


@app.post("/voice/jobs", response_model=JobAccepted, status_code=202)
def create_voice_job(request: VoiceJobRequest, background: BackgroundTasks) -> JobAccepted:
    # Both checked here rather than in the worker: an unknown voice and an
    # un-transcribed video are the caller's mistakes, and they deserve an
    # answer now instead of a failed job to go and read about later.
    chosen = voice.check_voice(request.voice or settings.voice)
    library.load_transcript(request.video_id)

    job_id = pipeline_db.create_job(request.video_id, "voice", request.callback_url)
    background.add_task(jobs.run_voice, job_id, request.video_id, chosen)
    return JobAccepted(job_id=job_id, video_id=request.video_id, state="queued")


@app.post("/render/jobs", response_model=JobAccepted, status_code=202)
def create_render_job(request: RenderJobRequest, background: BackgroundTasks) -> JobAccepted:
    preset_name = request.preset or settings.preset
    library.load_preset(preset_name)
    library.load_voice_track(request.video_id)
    if not pipeline_db.chunks_for(request.video_id):
        raise NoChunksError(
            f"no chunks for {request.video_id!r} — POST it to the transcript "
            f"service's /chunk first"
        )

    job_id = pipeline_db.create_job(request.video_id, "render", request.callback_url)
    background.add_task(
        jobs.run_render, job_id, request.video_id, preset_name, request.only_chunk
    )
    return JobAccepted(job_id=job_id, video_id=request.video_id, state="queued")


@app.post("/media-revision/jobs", response_model=JobAccepted, status_code=202)
def create_media_revision_job(
    request: MediaRevisionJobRequest, background: BackgroundTasks
) -> JobAccepted:
    if request.preset_id is not None:
        # Published editor config is validated (including allowlisted assets)
        # before a job row exists. A malformed revision is caller input, not a
        # background failure that n8n must wait to discover.
        visual_preset.load_published(request.preset_id, request.preset_revision or 0)
    else:
        library.load_preset(request.vertical_preset)
        library.load_preset(request.landscape_preset)
    library.load_voice_track(request.video_id)
    if not pipeline_db.chunks_for(request.video_id):
        raise NoChunksError(
            f"no chunks for {request.video_id!r} — POST it to the transcript "
            "service's /chunk first"
        )
    for brand_id in request.brand_ids:
        brand.load(brand_id)

    job_id = pipeline_db.create_job(
        request.video_id, "media_revision", request.callback_url
    )
    background.add_task(
        jobs.run_media_revision,
        job_id,
        request.video_id,
        request.render_revision,
        request.brand_ids,
        request.vertical_preset,
        request.landscape_preset,
        request.metadata_revision_id,
        request.preset_id,
        request.preset_revision,
    )
    return JobAccepted(job_id=job_id, video_id=request.video_id, state="queued")


@app.get("/jobs/{job_id}")
def read_job(job_id: str) -> JSONResponse:
    job = pipeline_db.get_job(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": f"no job {job_id!r}"})
    return JSONResponse(content=job)
