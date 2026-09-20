from fastapi.testclient import TestClient

from app.main import app


def test_external_api_requires_admin_password(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EXPOSE_HOST", "0.0.0.0")
    monkeypatch.setenv("ADMIN_PASSWORD", "secret")
    with TestClient(app) as client:
        assert client.get("/api/v1/health").status_code == 200
        assert client.get("/api/v1/models").status_code == 401
        assert client.get("/api/v1/models", headers={"X-Admin-Password": "secret"}).status_code == 200
