from fastapi.testclient import TestClient

import jobs
import main
import repository
from errors import MetadataError
from shared import pipeline_db


def test_missing_model_blocks_job_before_database_write(monkeypatch):
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    with TestClient(main.app) as client:
        response = client.post("/metadata/jobs", json={"video_id": "aaaaaaaaaaa"})
    assert response.status_code == 503


def test_duplicate_active_submission_reuses_job_without_starting_second_worker(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(pipeline_db, "DB_PATH", tmp_path / "pipeline.db")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.6-luna")
    runs = []
    monkeypatch.setattr(jobs, "run", lambda *args: runs.append(args))

    with TestClient(main.app) as client:
        first = client.post("/metadata/jobs", json={"video_id": "aaaaaaaaaaa"})
        second = client.post("/metadata/jobs", json={"video_id": "aaaaaaaaaaa"})

    assert first.status_code == 202
    assert first.json()["reused"] is False
    assert second.status_code == 202
    assert second.json()["reused"] is True
    assert second.json()["job_id"] == first.json()["job_id"]
    assert len(runs) == 1


def test_revision_endpoint_returns_selected_metadata_and_usage(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(pipeline_db, "DB_PATH", tmp_path / "pipeline.db")
    pipeline_db.init()
    monkeypatch.setattr(
        repository,
        "revision_bundle",
        lambda revision_id: {
            "revision": {"revision_id": revision_id, "state": "selected"},
            "items": [],
            "attempts": [],
            "usage": {"total_tokens": 0},
        },
    )

    with TestClient(main.app) as client:
        response = client.get("/metadata/revisions/revision-fixture")

    assert response.status_code == 200
    assert response.json()["revision"]["state"] == "selected"


def test_background_job_records_typed_safe_metadata_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_db, "DB_PATH", tmp_path / "pipeline.db")
    pipeline_db.init()
    job_id = pipeline_db.create_job("aaaaaaaaaaa", "metadata")
    monkeypatch.setattr(
        jobs.context,
        "load",
        lambda video_id: (_ for _ in ()).throw(
            MetadataError("translation_missing", "Translation is not ready.", retryable=False)
        ),
    )

    jobs.run(job_id, "aaaaaaaaaaa", "gpt-5.6-luna")

    job = pipeline_db.get_job(job_id)
    assert job["state"] == "failed"
    assert job["result"] == {"error_code": "translation_missing", "retryable": False}
    assert job["error"] == "Translation is not ready."


def test_successful_job_adds_soft_cost_warning_without_stopping(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_db, "DB_PATH", tmp_path / "pipeline.db")
    pipeline_db.init()
    job_id = pipeline_db.create_job("aaaaaaaaaaa", "metadata")
    monkeypatch.setattr(jobs.context, "load", lambda video_id: object())
    monkeypatch.setattr(jobs, "ResponsesClient", lambda **kwargs: object())
    monkeypatch.setattr(
        jobs.generator,
        "run",
        lambda source, client: {
            "revision_id": "revision-fixture",
            "state": "selected",
            "items": [],
        },
    )
    monkeypatch.setattr(
        jobs.repository,
        "revision_bundle",
        lambda revision_id: {
            "usage": {"input_tokens": 10_000, "output_tokens": 2_000, "total_tokens": 12_000}
        },
    )
    monkeypatch.setenv("METADATA_COST_WARNING_USD", "0.001")

    jobs.run(job_id, "aaaaaaaaaaa", "gpt-5.6-luna")

    job = pipeline_db.get_job(job_id)
    assert job["state"] == "done"
    assert job["result"]["estimated_cost_usd"] == 0.0044
    assert len(job["warnings"]) == 1
