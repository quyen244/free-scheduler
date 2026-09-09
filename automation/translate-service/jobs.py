"""Translation as a background job.

104 segments through a 1.8B model on CPU is minutes, not seconds, and n8n's
HTTP node gives up at 300 s. So the request records a job and returns, and the
work reports back later — the same shape ingest uses.
"""

import logging

import library
import translator
from shared import callbacks, pipeline_db

logger = logging.getLogger(__name__)

# The kinds this service performs, and therefore the only ones it may declare
# dead on startup. A service that reaped every unfinished row would kill the
# other services' live jobs every time it restarted.
OWNED_KINDS = ("translate",)


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
            error="translate-service restarted while this job was running, "
            "so the work was lost",
        )
        _notify(job_id)


def run_translate(job_id: str, video_id: str) -> None:
    pipeline_db.mark_running(job_id)

    try:
        transcript = library.load_transcript(video_id)
        source_language = str(transcript.get("language") or "")
        # What the budget pass did, per segment it touched. Reported so the
        # decision is auditable after the fact instead of only in the log.
        fitted: list[dict] = []
        segments = translator.translate_segments(
            transcript.get("segments") or [], source_language, fitted
        )

        payload = {
            **transcript,
            "language": translator.TARGET_LANGUAGE,
            # Kept so a later stage can tell a real translation from a
            # Vietnamese source that passed straight through.
            "source_language": source_language,
            "segments": segments,
            "transcript": " ".join(str(segment["text"]) for segment in segments),
        }
        path = library.save_translation(video_id, payload)
        try:
            library.save_budget_report(video_id, fitted)
        except OSError as exc:
            # The report is evidence, not the deliverable. A translation that
            # succeeded must not be thrown away because an audit file could not
            # be written.
            logger.warning(
                "could not write the budget report for %s: %s", video_id, exc
            )
    except Exception as exc:  # noqa: BLE001 — top of the worker; nothing above it catches
        logger.exception("translate job %s failed", job_id)
        pipeline_db.finish_job(job_id, "failed", error=f"{type(exc).__name__}: {exc}")
    else:
        pipeline_db.advance_stage(video_id, "translated")
        pipeline_db.finish_job(
            job_id,
            "done",
            result={
                "video_id": video_id,
                "language": translator.TARGET_LANGUAGE,
                "source_language": source_language,
                "total_segments": len(segments),
                "transcript_path": str(path),
                "over_slot_segments": len(fitted),
                "shortened_segments": sum(
                    1 for entry in fitted if entry["after_chars"] < entry["before_chars"]
                ),
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
