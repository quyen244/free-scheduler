"""Ingest as a background job.

An 11-minute source took 2m18s to download; n8n's HTTP node gives up at 300 s.
So the request records a job and returns, and the work reports back later.
"""

import logging

import media
from shared import callbacks, pipeline_db

logger = logging.getLogger(__name__)

# The job kinds this service performs, and therefore the only ones it may
# declare dead on startup. See reap_orphans.
OWNED_KINDS = ("ingest",)


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
            error="media-service restarted while this job was running, so the work was lost",
        )
        # Its half-downloaded bytes are now garbage that nothing else collects:
        # the retention sweep only ever looks at videos that finished.
        media.clear_partials(str(job["video_id"]))
        _notify(job_id)


def run_ingest(
    job_id: str,
    url: str,
) -> None:
    pipeline_db.mark_running(job_id)

    try:
        record, cached = media.get_or_download(url)
    except Exception as exc:  # noqa: BLE001 — top of the worker; nothing above it catches
        logger.exception("ingest job %s failed", job_id)
        pipeline_db.finish_job(job_id, "failed", error=f"{type(exc).__name__}: {exc}")
    else:
        directory = media.video_dir(str(record["video_id"]))
        pipeline_db.finish_job(
            job_id,
            "done",
            result={
                "video_id": record["video_id"],
                "title": record["title"],
                "duration_s": record["duration_s"],
                "raw_path": str(directory / media.RAW_NAME),
                "audio_path": str(directory / media.AUDIO_NAME),
                "cached": cached,
                "width": record["width"],
                "height": record["height"],
                "source_hash": record["source_hash"],
            },
        )

    # Outside the branch, deliberately. A failure that does not call back parks
    # an n8n execution on a resume that never comes: nothing turns red, nothing
    # alerts, and it is found days later by wondering where a video went.
    _notify(job_id)


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
