"""Delivering a finished job back to whatever asked to be told.

Shared by every service that runs jobs, for the same reason ``pipeline_db`` is:
the behaviour has to be identical everywhere, and three copies drift.
"""

import logging
import time

import httpx

logger = logging.getLogger(__name__)

TIMEOUT_S = 15.0

# A fast job can call back before n8n has registered the Wait node. Depending
# on the timing, n8n answers 404 or 409. Retry both, as well as transient 5xx
# responses, and count only 2xx responses as delivered.
RETRYABLE_STATUSES = {404, 409, 500, 502, 503, 504}
RETRY_DELAYS_S = (0.5, 1.0, 2.0, 4.0, 8.0)


def deliver(callback_url: str, job: dict[str, object]) -> bool:
    """POST `job` to `callback_url`. Returns whether it was delivered.

    Never raises. The job itself is finished and its result is stored, so a
    failure here is recoverable by polling ``GET /jobs/{id}``; raising would
    only lose the exception inside a background task.
    """
    for attempt, delay in enumerate((0.0, *RETRY_DELAYS_S)):
        if delay:
            time.sleep(delay)
        try:
            response = httpx.post(callback_url, json=job, timeout=TIMEOUT_S)
        except httpx.HTTPError as exc:
            logger.warning(
                "callback to %s failed (attempt %d): %s", callback_url, attempt + 1, exc
            )
            continue

        if response.status_code in RETRYABLE_STATUSES:
            logger.info(
                "callback to %s returned retryable status %d (attempt %d)",
                callback_url,
                response.status_code,
                attempt + 1,
            )
            continue

        logger.info("callback to %s returned %d", callback_url, response.status_code)
        return 200 <= response.status_code < 300

    logger.error(
        "gave up calling back to %s; the job is still readable at GET /jobs/{id}",
        callback_url,
    )
    return False
