"""Run media ingest as an asynchronous, retryable background job."""

import logging
import time

import media
from errors import (
    AudioExtractionError,
    DownloadError,
    InvalidURLError,
    SourcePolicyError,
)
from shared import callbacks, pipeline_db

logger = logging.getLogger(__name__)

# The job kinds this service performs, and therefore the only ones it may
# declare dead on startup. See reap_orphans.
OWNED_KINDS = ("ingest",)

# One initial attempt plus the two automatic retries approved for ingest.
MAX_ATTEMPTS = 3
RETRY_DELAYS_S = (5.0, 20.0)
RETRYABLE_ERRORS = (DownloadError, AudioExtractionError)


def reap_orphans() -> None:
    """Fail and report jobs whose worker disappeared during a restart."""
    for job in pipeline_db.unfinished_jobs(OWNED_KINDS):
        job_id = str(job["job_id"])
        logger.warning("job %s was orphaned by a restart; failing it", job_id)
        pipeline_db.finish_job(
            job_id,
            "failed",
            result={"error_code": "ingest_interrupted", "retryable": True},
            error="media-service restarted while this job was running, so the work was lost",
        )
        # A killed process cannot clean its own partial download. Remove only
        # the partial files owned by this video's ingest job.
        media.clear_partials(str(job["video_id"]))
        _notify(job_id)


def run_ingest(job_id: str, url: str) -> None:
    """Ingest once, retrying only the approved transient failure classes."""
    pipeline_db.mark_running(job_id)

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            record, cached = media.get_or_download(url)
        except RETRYABLE_ERRORS as exc:
            if attempt < MAX_ATTEMPTS:
                delay = RETRY_DELAYS_S[attempt - 1]
                logger.warning(
                    "ingest job %s attempt %d/%d failed with %s; retrying in %.0fs",
                    job_id,
                    attempt,
                    MAX_ATTEMPTS,
                    exc.code,
                    delay,
                )
                time.sleep(delay)
                continue
            logger.warning(
                "ingest job %s exhausted %d attempts with %s",
                job_id,
                MAX_ATTEMPTS,
                exc.code,
            )
            pipeline_db.finish_job(
                job_id,
                "failed",
                result={
                    "error_code": exc.code,
                    "retryable": True,
                    "attempts": attempt,
                },
                error=f"{type(exc).__name__}: {exc}",
            )
        except (InvalidURLError, SourcePolicyError) as exc:
            logger.warning("ingest job %s rejected with %s", job_id, exc.code)
            pipeline_db.finish_job(
                job_id,
                "failed",
                result={
                    "error_code": exc.code,
                    "retryable": False,
                    "attempts": attempt,
                },
                error=f"{type(exc).__name__}: {exc}",
            )
        except Exception:  # noqa: BLE001 - terminal background boundary
            logger.exception("ingest job %s failed internally", job_id)
            pipeline_db.finish_job(
                job_id,
                "failed",
                result={
                    "error_code": "ingest_internal",
                    "retryable": True,
                    "attempts": attempt,
                },
                error="Ingest failed internally; retry the ingest stage.",
            )
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
                    "attempts": attempt,
                },
            )
        break

    # Success and failure both call back. Otherwise a failed background job
    # parks n8n on its Wait node forever.
    _notify(job_id)


def _notify(job_id: str) -> None:
    """Deliver the exact persisted terminal job payload to its callback."""
    job = pipeline_db.get_job(job_id)
    if job is None:
        logger.error("job %s vanished before its callback", job_id)
        return

    callback_url = job["callback_url"]
    if not callback_url:
        return

    if callbacks.deliver(str(callback_url), job):
        logger.info("called back for job %s (%s)", job_id, job["state"])
