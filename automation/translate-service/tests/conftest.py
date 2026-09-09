"""Preconditions for the translate suite.

One of these tests translates the 11-minute reference video for real, and the
translate job writes `transcript.vi.json` in place. That file is the input to
chunking and to the voice stage, so running this suite while a pipeline run is
in flight replaces the transcript underneath a stage that is still reading it —
the run keeps going and produces a track built from two different translations.
Nothing fails, and the artefacts no longer describe one run.

It is not hypothetical: it happened during this cycle's benchmark, at 60 % of a
22-minute voice stage. The run survived only because the job was killed before
it finished writing.

So the suite refuses to start while any job is running. A refusal costs a
re-run; the alternative costs a benchmark and, worse, is not obvious afterwards.
"""

import pytest

from shared import pipeline_db

# The videos this suite writes to. Both are read from the shared volume, so a
# concurrent job on either is a conflict.
GUARDED_VIDEO_IDS = ("3gi_15UH9fQ", "jNQXAC9IVRw")

# The kinds that write files this suite also writes, or reads from them.
GUARDED_KINDS = ("ingest", "transcribe", "translate", "chunk", "voice", "render")


def _jobs_in_flight() -> list[dict[str, object]]:
    try:
        running = pipeline_db.unfinished_jobs(GUARDED_KINDS)
    except Exception as exc:  # noqa: BLE001 - a guard must not itself break the suite
        # An unreadable database is not proof that nothing is running, but it is
        # also not a reason to refuse. Say so and let the suite proceed.
        print(f"WARNING: could not check for running jobs ({exc})")
        return []
    # `unfinished_jobs` already restricts to queued and running rows, and it
    # returns only `job_id` and `video_id` - so the video is the whole filter.
    return [job for job in running
            if str(job.get("video_id")) in GUARDED_VIDEO_IDS]


@pytest.fixture(scope="session", autouse=True)
def no_pipeline_run_in_flight() -> None:
    busy = _jobs_in_flight()
    if busy:
        detail = ", ".join(
            f"{job.get('video_id')} (job {job.get('job_id')})" for job in busy
        )
        pytest.exit(
            "REFUSING to run: a pipeline job is still in flight "
            f"({detail}). This suite rewrites transcript.vi.json for "
            f"{' and '.join(GUARDED_VIDEO_IDS)}, which would replace that "
            "job's input while it reads it. Wait for the run, or stop it "
            "first.",
            returncode=2,
        )
