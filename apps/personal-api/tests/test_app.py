from fastapi.testclient import TestClient
from personal_api.main import app


def test_core_health_mounted():
    res = TestClient(app).get("/api/core/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
