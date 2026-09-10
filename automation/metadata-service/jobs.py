import logging
import os
from decimal import Decimal

import context
import generator
import repository
from errors import MetadataError
from openai_client import ResponsesClient
from shared import callbacks, pipeline_db


logger = logging.getLogger(__name__)
OWNED_KINDS = ("metadata",)


def reap_orphans() -> None:
    for job in pipeline_db.unfinished_jobs(OWNED_KINDS):
        job_id = str(job["job_id"])
        repository.abandon_running_attempts(str(job["video_id"]))
        pipeline_db.finish_job(
            job_id,
            "failed",
            error="metadata-service restarted while this job was running; retry the metadata stage",
        )
        _notify(job_id)


def run(job_id: str, video_id: str, model: str) -> None:
    pipeline_db.mark_running(job_id)
    try:
        source = context.load(video_id)
        client = ResponsesClient(
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            model=model,
        )
        result = generator.run(source, client)
    except MetadataError as exc:
        logger.warning("metadata job %s failed with %s", job_id, exc.code)
        pipeline_db.finish_job(
            job_id,
            "failed",
            result={"error_code": exc.code, "retryable": exc.retryable},
            error=str(exc),
        )
    except Exception as exc:  # noqa: BLE001 - terminal boundary for background work
        logger.exception("metadata job %s failed", job_id)
        pipeline_db.finish_job(
            job_id,
            "failed",
            result={"error_code": "metadata_internal", "retryable": True},
            error="Metadata generation failed internally; retry the metadata stage.",
        )
    else:
        usage = repository.revision_bundle(result["revision_id"])["usage"]
        input_rate = Decimal(os.environ.get("OPENAI_INPUT_USD_PER_MILLION", "0.20"))
        output_rate = Decimal(os.environ.get("OPENAI_OUTPUT_USD_PER_MILLION", "1.20"))
        warning_threshold = Decimal(os.environ.get("METADATA_COST_WARNING_USD", "0.02"))
        estimated_cost = (
            Decimal(usage["input_tokens"]) * input_rate
            + Decimal(usage["output_tokens"]) * output_rate
        ) / Decimal(1_000_000)
        result = {
            **result,
            "usage": usage,
            "estimated_cost_usd": float(estimated_cost),
        }
        warnings = (
            [
                "Metadata cost warning: estimated cost "
                f"${estimated_cost:.6f} reached the ${warning_threshold:.2f} threshold."
            ]
            if estimated_cost >= warning_threshold
            else None
        )
        state = "done" if result["state"] == "selected" else "failed"
        pipeline_db.finish_job(
            job_id,
            state,
            result=(
                result
                if state == "done"
                else {**result, "error_code": "metadata_items_need_action", "retryable": True}
            ),
            error=None if state == "done" else "One or more metadata items need action.",
            warnings=warnings,
        )
    _notify(job_id)


def _notify(job_id: str) -> None:
    job = pipeline_db.get_job(job_id)
    if not job or not job.get("callback_url"):
        return
    callbacks.deliver(str(job["callback_url"]), job)
