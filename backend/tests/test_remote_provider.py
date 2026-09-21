import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.providers.openai_compatible import OpenAICompatibleProvider, validate_headers
from app.services.secrets import SecretService


def test_secret_service_keeps_master_key_outside_database(tmp_path, monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_SECRET_KEY", raising=False)
    service = SecretService(tmp_path)
    encrypted = service.encrypt("top-secret")
    assert encrypted != "top-secret"
    assert service.decrypt(encrypted) == "top-secret"
    assert (tmp_path / "secrets" / "master.key").stat().st_mode & 0o077 == 0


def test_forbidden_custom_headers_are_rejected():
    with pytest.raises(ValueError, match="not allowed"):
        validate_headers({"Host": "evil.example"}, "secret")
    assert validate_headers({"x-api-key": "{api_key}"}, "secret")["x-api-key"] == "secret"


@pytest.mark.asyncio
async def test_openai_compatible_stream_is_normalized():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text='data: {"choices":[{"delta":{"content":"Bon"}}]}\n\ndata: {"choices":[{"delta":{"content":"jour"}}]}\n\ndata: [DONE]\n\n')

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider("https://remote.test/v1", "secret", "model", client=client)
    events = [event async for event in provider.run({"name": "model"}, [{"role": "user", "content": "x"}], {"job_id": 7})]
    assert [event.type for event in events] == ["token", "token", "completed"]
    assert json.loads(requests[0].content)["model"] == "model"
    assert requests[0].headers["authorization"] == "Bearer secret"
    await client.aclose()


def test_service_api_never_returns_encrypted_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EXPOSE_HOST", "127.0.0.1")

    async def online(self):
        return True

    monkeypatch.setattr(OpenAICompatibleProvider, "health", online)
    with TestClient(app) as client:
        response = client.post("/api/v1/services", json={"name": "remote", "url": "https://api.example.test/v1", "model": "remote-model", "api_key": "secret"})
        assert response.status_code == 200
        assert "api_key" not in response.json()
        services = client.get("/api/v1/services").json()
        assert "api_key_enc" not in services[-1]
        assert "extra_headers_enc" not in services[-1]
