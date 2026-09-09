from fastapi.testclient import TestClient

from main import app


def test_health_reports_model_loaded_true_once_startup_completes():
    """T2: the Whisper model must be loaded during app startup (FastAPI
    lifespan), so /health reflects real readiness, not a hardcoded stub."""
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "model_loaded": True}
