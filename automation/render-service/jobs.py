"""Voicing and rendering as background jobs.

Both run for tens of minutes — synthesis is slower than real time on CPU, and
an 11-minute video is three chunks of ffmpeg — while n8n's HTTP node gives up
at 300 s. So the request records a job and returns, and the work reports back
later, the same shape ingest and translation use.
"""

import logging

import library
import manifest
import render
import variants
import voice
from errors import NoChunksError, RenderError
from shared import callbacks, pipeline_db

logger = logging.getLogger(__name__)

# The kinds this service performs, and therefore the only ones it may declare
# dead on startup. A service that reaped every unfinished row would kill the
# other services' live jobs every time it restarted.
OWNED_KINDS = ("voice", "render", "media_revision")


def reap_orphans() -> None:
    """Fail jobs that were in flight when this process last stopped.

    A restart takes the work with it but leaves the row saying 'running', and
    nothing is left alive to call back — so the n8n execution waits on a resume
    that can no longer come. Startup is the last place that can notice.
    """
    for job in pipeline_db.unfinished_jobs(OWNED_KINDS):
        job_id = str(job["job_id"])
        logger.warning("job %s was orphaned by a restart; failing it", job_id)
        pipeline_db.finish_job(
            job_id,
            "failed",
            error="render-service restarted while this job was running, "
            "so the work was lost",
        )
        _notify(job_id)


def run_voice(job_id: str, video_id: str, chosen_voice: str) -> None:
    pipeline_db.mark_running(job_id)

    try:
        manifest = voice.build_track(
            video_id,
            chosen_voice,
            on_progress=lambda done: pipeline_db.set_progress(job_id, done),
        )
    except Exception as exc:  # noqa: BLE001 — top of the worker; nothing above it catches
        logger.exception("voice job %s failed", job_id)
        pipeline_db.finish_job(job_id, "failed", error=f"{type(exc).__name__}: {exc}")
    else:
        pipeline_db.finish_job(
            job_id,
            "done",
            result={
                "video_id": video_id,
                "voice": manifest["voice"],
                "duration_s": manifest["duration_s"],
                "total_segments": manifest["total_segments"],
                "voice_path": str(library.voice_path(video_id)),
                "manifest_path": str(
                    library.video_dir(video_id) / library.VOICE_MANIFEST_NAME
                ),
            },
            # Reported, never silently shipped. A segment that needs 1.9x to
            # fit its slot is the difference between "the voice sounds a bit
            # fast" and finding out from a comment.
            warnings=list(manifest["warnings"]),  # type: ignore[arg-type]
        )

    # Outside the branch, deliberately. A failure that does not call back parks
    # an n8n execution on a resume that never comes: nothing turns red, nothing
    # alerts, and it is found days later by wondering where a video went.
    _notify(job_id)


def run_render(job_id: str, video_id: str, preset_name: str, only_chunk: int | None) -> None:
    pipeline_db.mark_running(job_id)

    try:
        result, warnings = _render(job_id, video_id, preset_name, only_chunk)
    except Exception as exc:  # noqa: BLE001 — top of the worker; nothing above it catches
        logger.exception("render job %s failed", job_id)
        pipeline_db.finish_job(job_id, "failed", error=f"{type(exc).__name__}: {exc}")
    else:
        pipeline_db.finish_job(job_id, "done", result=result, warnings=warnings)

    _notify(job_id)


def run_media_revision(
    job_id: str,
    video_id: str,
    render_revision: int,
    brand_ids: list[str],
    vertical_preset: str,
    landscape_preset: str,
    metadata_revision_id: str | None,
) -> None:
    """Build the complete revision and always leave a pollable/callback result."""
    pipeline_db.mark_running(job_id)
    try:
        media_manifest = variants.render_media_revision(
            video_id,
            render_revision,
            brand_ids=brand_ids,
            vertical_preset_name=vertical_preset,
            landscape_preset_name=landscape_preset,
            metadata_revision_id=metadata_revision_id,
            on_progress=lambda done: pipeline_db.set_progress(job_id, done),
        )
    except Exception as exc:  # worker boundary; every failure must call back
        logger.exception("media revision job %s failed", job_id)
        pipeline_db.finish_job(job_id, "failed", error=f"{type(exc).__name__}: {exc}")
    else:
        result = {
            "video_id": video_id,
            "render_revision": render_revision,
            "manifest_state": media_manifest.state,
            "manifest_path": str(manifest.current_manifest_path(video_id)),
            "revision_manifest_path": str(
                manifest.manifest_revision_path(video_id, render_revision)
            ),
            "asset_count": len(media_manifest.assets),
            "failure_count": len(media_manifest.failures),
            # Which encoder actually ran, and why it was not the preferred one.
            # A CPU fallback turns minutes into hours, so it travels with the
            # result instead of living only in the container log.
            "encoding": render.encoder_choice().as_dict(),
            "assets": [asset.model_dump(mode="json") for asset in media_manifest.assets],
            "failures": [failure.model_dump(mode="json") for failure in media_manifest.failures],
        }
        if media_manifest.state == "ready":
            pipeline_db.advance_stage(video_id, "rendered")
            pipeline_db.finish_job(
                job_id, "done", result=result, warnings=media_manifest.warnings
            )
        else:
            pipeline_db.finish_job(
                job_id,
                "failed",
                result=result,
                error="media revision needs action; inspect its typed failures",
                warnings=media_manifest.warnings,
            )
    _notify(job_id)


def _render(
    job_id: str, video_id: str, preset_name: str, only_chunk: int | None
) -> tuple[dict[str, object], list[str]]:
    preset = library.load_preset(preset_name)
    pipeline_db.set_preset(video_id, preset_name)

    chunks = pipeline_db.chunks_for(video_id)
    if only_chunk is not None:
        chunks = [chunk for chunk in chunks if int(chunk["idx"]) == only_chunk]
    if not chunks:
        raise NoChunksError(
            f"no chunks for {video_id!r} — POST it to the transcript "
            f"service's /chunk first"
        )

    segments = library.load_transcript(video_id).get("segments") or []

    if only_chunk is None:
        render.drop_surplus_chunk_dirs(video_id, keep=len(chunks))

    rendered: list[dict[str, object]] = []
    failures: list[str] = []
    for position, chunk in enumerate(chunks):
        idx = int(chunk["idx"])
        try:
            outcome = render.render_chunk(
                video_id,
                chunk,
                preset,
                segments,
                # Empty until F8 writes them; the preset's boxes are simply not
                # drawn when there is no text for them.
                texts={
                    "caption_top": str(chunk.get("hook") or ""),
                    "caption_bottom": str(chunk.get("caption") or ""),
                },
            )
        except RenderError as exc:
            logger.error("chunk %d of %s failed: %s", idx, video_id, exc)
            pipeline_db.mark_chunk_failed(video_id, idx)
            failures.append(f"chunk {idx}: {exc}")
        else:
            pipeline_db.mark_chunk_rendered(video_id, idx, str(outcome["final_path"]))
            rendered.append(outcome)
        pipeline_db.set_progress(job_id, (position + 1) / len(chunks))

    if failures:
        # The chunks that did render keep their rows and their files, so the
        # retry is cheap — but the job fails, because a partly rendered video
        # that reports success is one that gets uploaded with a hole in it.
        raise RenderError("; ".join(failures))

    clips = [library.chunk_dir(video_id, int(chunk["idx"])) / render.CLIP_NAME for chunk in chunks]
    if len(clips) > 1:
        processed_path = str(render.concat(video_id, clips))
    else:
        # One chunk is already the whole video. Copying it to processed/ would
        # double the bytes on disk to say the same thing twice.
        processed_path = str(clips[0])

    if only_chunk is None:
        # Only when the whole video rendered. A one-chunk trial run that moved
        # the stage to `rendered` would tell F9 there is nothing left to do,
        # and the other chunks would never be built.
        pipeline_db.advance_stage(video_id, "rendered")

    manifest = library.load_voice_manifest(video_id) or {}
    result = {
        "video_id": video_id,
        "preset": preset_name,
        "processed_path": processed_path,
        "total_chunks": len(rendered),
        "encoder": render.encoder(),
        "chunks": rendered,
    }
    return result, list(manifest.get("warnings") or [])


def _notify(job_id: str) -> None:
    """POST the finished job to whatever asked to be told.

    The body is read back from the database rather than passed in, so the
    callback and `GET /jobs/{id}` can never describe the job differently.
    """
    job = pipeline_db.get_job(job_id)
    if job is None:
        logger.error("job %s vanished before its callback", job_id)
        return

    callback_url = job["callback_url"]
    if not callback_url:
        return

    if callbacks.deliver(str(callback_url), job):
        logger.info("called back for job %s (%s)", job_id, job["state"])
