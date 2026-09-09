from fastapi.testclient import TestClient

import transcriber
from main import app


def test_health_endpoint_answers_without_waiting_for_the_model(monkeypatch):
    """/health must answer as soon as the process is up.

    Whisper takes tens of seconds to load, and the compose healthcheck starts
    probing before that finishes. The model state is reported, not required —
    so this pins the un-loaded case explicitly. Pinned with monkeypatch rather
    than read from the live module: any earlier test that transcribed anything
    leaves the model cached in-process, and this would otherwise pass or fail
    depending on which file pytest collected first.
    """
    monkeypatch.setattr(transcriber, "_model", None)

    # Bare client, not the context manager: entering it runs the lifespan,
    # which loads the model — the exact thing this test says is not needed.
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "model_loaded": False}
