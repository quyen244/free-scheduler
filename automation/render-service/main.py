import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

import jobs
import matting
import matting_jobs
import brand
import brand_assets
import brands
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


# ---------------------------------------------------------------------------
# brands: artwork and layout in one folder, which is what a render loops over
# ---------------------------------------------------------------------------


@app.get("/brands")
def list_brand_configs() -> dict[str, object]:
    return {"brands": brands.list_brands()}


@app.post("/brands", status_code=201)
def create_brand(payload: dict[str, object]) -> dict[str, object]:
    created = brands.create(
        str(payload.get("brand_id") or ""), str(payload.get("display_name") or "")
    )
    return created.model_dump(mode="json", exclude_none=True)


@app.get("/brands/{brand_id}/draft")
def get_brand_draft(brand_id: str) -> dict[str, object]:
    return brands.load_draft(brand_id).model_dump(mode="json", exclude_none=True)


@app.post("/brands/{brand_id}/draft")
def save_brand_draft(brand_id: str, payload: dict[str, object]) -> dict[str, object]:
    if str(payload.get("brand_id")) != brand_id:
        raise RenderError("the draft body must carry the brand id it is saved under")
    return brands.save_draft(payload).model_dump(mode="json", exclude_none=True)


@app.post("/brands/{brand_id}/publish")
def publish_brand(brand_id: str, payload: dict[str, object]) -> dict[str, object]:
    if str(payload.get("brand_id")) != brand_id:
        raise RenderError("the draft body must carry the brand id it is published under")
    return brands.publish(payload).model_dump(mode="json", exclude_none=True)


def _revision(brand_id: str, revision: str) -> int:
    """Read a revision out of a URL, where "latest" is a legal spelling.

    Resolved here and nowhere later: everything downstream receives the number,
    so a stored manifest can never say "latest" and start meaning a different
    layout the next time the brand is published.
    """
    if revision == "latest":
        return brands.latest_revision(brand_id)
    if not revision.isdigit():
        # 422, like FastAPI's own answer when this path segment was typed `int`:
        # a misspelled revision is caller input, not a render that went wrong.
        raise BrandConfigError("a brand revision is a positive integer or 'latest'")
    return int(revision)


@app.get("/brands/{brand_id}/latest")
def get_brand_latest_revision(brand_id: str) -> dict[str, object]:
    """The number "latest" means right now, without fetching the layout itself."""
    revision = brands.latest_revision(brand_id)
    published = brands.load_published(brand_id, revision)
    return {
        "brand_id": brand_id,
        "revision": revision,
        "content_sha256": published.content_sha256,
    }


@app.get("/brands/{brand_id}/revisions/{revision}")
def get_brand_revision(brand_id: str, revision: str) -> dict[str, object]:
    return brands.load_published(brand_id, _revision(brand_id, revision)).model_dump(
        mode="json", exclude_none=True
    )


@app.get("/brands/{brand_id}/render-config/{revision}/{aspect}")
def get_brand_render_config(brand_id: str, revision: str, aspect: str) -> dict[str, object]:
    """What the renderer will actually receive. Useful for verifying a layout."""
    if aspect not in ("vertical", "landscape"):
        raise RenderError("aspect must be 'vertical' or 'landscape'")
    published = brands.load_published(brand_id, _revision(brand_id, revision))
    return brands.render_config(published, aspect)  # type: ignore[arg-type]


@app.post("/brands/{brand_id}/assets", status_code=201)
async def upload_brand_asset(
    brand_id: str,
    request: Request,
    asset_id: str,
    filename: str,
    role: str = "other",
) -> dict[str, object]:
    """Store one uploaded file. The caller then places it as a layer.

    The body is the raw bytes rather than a multipart form: multipart would
    pull `python-multipart` into the image for a single endpoint that only ever
    carries one file, and the browser can post a File object directly.

    Deliberately does not touch the draft: a file on disk that no layer points
    at is harmless, whereas a draft pointing at a file that failed to write is
    a publish that fails much later for a reason nobody can see.
    """
    body = await request.body()
    try:
        return brand_assets.store(brand_id, asset_id, filename, body, role)
    except ValueError as exc:
        # The caller sent an empty body or a file type no layer can use. That
        # is their mistake to fix, not an upstream failure, so it answers 422
        # rather than the 502 a RenderError would produce.
        raise BrandConfigError(str(exc)) from exc


@app.get("/brands/{brand_id}/assets/{filename}")
def read_brand_asset(brand_id: str, filename: str) -> FileResponse:
    path = brands.asset_file(brand_id, filename)
    if not path.is_file():
        raise PresetNotFoundError(f"no such brand asset: {filename}")
    return FileResponse(path)


@app.post("/brands/{brand_id}/matting")
def mat_brand_host(brand_id: str, payload: dict[str, object]) -> dict[str, object]:
    """Matte a presenter video that is already stored in this brand.

    Synchronous: the alpha mask has to land beside its video before the asset
    record can name both, and there is nothing for the editor to draw until it
    does.
    """
    try:
        return brand_assets.mat_host(
            brand_id,
            str(payload.get("file") or ""),
            list(payload.get("corrections") or []),
        )
    except FileNotFoundError as exc:
        raise PresetNotFoundError(str(exc)) from exc


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


def _resolve_revisions(requested: dict[str, int | str]) -> dict[str, int]:
    """Turn what the caller asked for into the numbers the job will remember.

    Once, at acceptance, and only the numbers travel on: a brand with nothing
    published refuses the job here rather than falling back to its draft, and a
    stored manifest can never say "latest" and start meaning something else the
    next time someone publishes.
    """
    return {
        brand_id: (
            brands.latest_revision(brand_id) if revision == "latest" else int(revision)
        )
        for brand_id, revision in requested.items()
    }


@app.post("/media-revision/jobs", response_model=JobAccepted, status_code=202)
def create_media_revision_job(
    request: MediaRevisionJobRequest, background: BackgroundTasks
) -> JobAccepted:
    brand_revisions: dict[str, int] | None = None
    if request.brand_revisions is not None:
        brand_revisions = _resolve_revisions(request.brand_revisions)
        # Every named brand revision is resolved to a render config here, so a
        # draft, a missing file or a layout with no visible footage answers the
        # caller now instead of failing a background job minutes later.
        for brand_id, brand_revision in brand_revisions.items():
            published = brands.load_published(brand_id, brand_revision)
            for aspect in ("landscape", "vertical"):
                brands.render_config(published, aspect)
    elif request.preset_id is not None:
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
    if request.brand_revisions is None:
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
        brand_revisions,
    )
    return JobAccepted(
        job_id=job_id,
        video_id=request.video_id,
        state="queued",
        # What "latest" turned into, so the caller can record the same numbers
        # against its own campaign without asking a second time.
        brand_revisions=brand_revisions,
    )


@app.get("/jobs/{job_id}")
def read_job(job_id: str) -> JSONResponse:
    job = pipeline_db.get_job(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": f"no job {job_id!r}"})
    return JSONResponse(content=job)
