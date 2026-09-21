import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.database.database import Database
from app.errors import AppError
from app.main import app
from app.orchestrator.jobs import JobManager, create_job
from app.orchestrator.resource_manager import ResourceManager
from app.providers.base import ProviderEvent
from app.providers.comfyui import ComfyUIProvider
from app.services.image_outputs import MAX_OUTPUT_BYTES, output_path, save_output
from app.services.image_workflows import apply_overrides, load_workflow, list_workflows


MIGRATIONS = Path(__file__).parents[1] / "app" / "database" / "migrations"
PNG = b"\x89PNG\r\n\x1a\nimage"


@pytest.mark.asyncio
async def test_comfyui_provider_submits_polls_and_downloads_output():
    async def handler(request):
        if request.url.path == "/prompt":
            assert json.loads(request.content)["client_id"] == "orchestrator-7"
            return httpx.Response(200, json={"prompt_id": "prompt-1"})
        if request.url.path == "/history/prompt-1":
            return httpx.Response(200, json={"prompt-1": {"status": {"completed": True}, "outputs": {"9": {"images": [{"filename": "result.png", "subfolder": "", "type": "output"}]}}}})
        if request.url.path == "/view":
            return httpx.Response(200, headers={"content-type": "image/png"}, content=PNG)
        raise AssertionError(request.url)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://comfyui")
    provider = ComfyUIProvider("http://comfyui", client=client, poll_interval=0)
    events = [event async for event in provider.run_image({"1": {}}, 7)]
    content, media_type = await provider.download_output(events[1].data)

    assert [event.type for event in events] == ["progress", "output", "completed"]
    assert content == PNG
    assert media_type == "image/png"
    await client.aclose()


@pytest.mark.asyncio
async def test_comfyui_provider_rejects_unsafe_output_path():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200)), base_url="http://comfyui")
    provider = ComfyUIProvider("http://comfyui", client=client)

    with pytest.raises(AppError, match="Nom de sortie"):
        await provider.download_output({"filename": "../secret.png", "subfolder": "", "type": "output"})
    await client.aclose()


def test_reference_workflow_is_versioned_and_overrides_are_limited():
    assert list_workflows() == [{"id": "reference", "label": "Reference SDXL"}]
    workflow = apply_overrides(load_workflow("reference"), prompt="un chat", seed=42, steps=12, cfg=5.5, width=512, height=768)

    assert workflow["6"]["inputs"]["text"] == "un chat"
    assert workflow["4"]["inputs"]["seed"] == 42
    assert workflow["4"]["inputs"]["steps"] == 12
    assert workflow["5"]["inputs"]["width"] == 512
    assert workflow["5"]["inputs"]["height"] == 768


@pytest.mark.asyncio
async def test_comfyui_provider_rejects_node_errors():
    async def handler(request):
        return httpx.Response(200, json={"prompt_id": None, "node_errors": {"4": {"message": "bad"}}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://comfyui")
    provider = ComfyUIProvider("http://comfyui", client=client, poll_interval=0)
    with pytest.raises(AppError, match="rejeté"):
        async for _ in provider.run_image({"1": {}}, 1):
            pass
    await client.aclose()


@pytest.mark.asyncio
async def test_comfyui_provider_reports_failed_status():
    async def handler(request):
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "prompt-9"})
        return httpx.Response(200, json={"prompt-9": {"status": {"status_str": "error"}, "outputs": {}}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://comfyui")
    provider = ComfyUIProvider("http://comfyui", client=client, poll_interval=0)
    with pytest.raises(AppError, match="échoué"):
        async for _ in provider.run_image({"1": {}}, 1):
            pass
    await client.aclose()


@pytest.mark.asyncio
async def test_comfyui_provider_enforces_output_limit():
    async def handler(request):
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "prompt-1"})
        images = [{"filename": f"{index}.png", "subfolder": "", "type": "output"} for index in range(3)]
        return httpx.Response(200, json={"prompt-1": {"status": {"completed": True}, "outputs": {"9": {"images": images}}}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://comfyui")
    provider = ComfyUIProvider("http://comfyui", client=client, poll_interval=0, max_outputs=2)
    with pytest.raises(AppError, match="trop de sorties"):
        async for _ in provider.run_image({"1": {}}, 1):
            pass
    await client.aclose()


@pytest.mark.asyncio
async def test_comfyui_provider_rejects_unsafe_subfolder_and_content_type():
    async def handler(request):
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"nope")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://comfyui")
    provider = ComfyUIProvider("http://comfyui", client=client)
    with pytest.raises(AppError, match="Chemin"):
        await provider.download_output({"filename": "result.png", "subfolder": "../secret", "type": "output"})
    with pytest.raises(AppError, match="non autorisé"):
        await provider.download_output({"filename": "result.png", "subfolder": "", "type": "output"})
    await client.aclose()


def test_image_output_security_validates_signatures_and_paths(tmp_path):
    metadata = save_output(tmp_path, 7, 1, PNG, "image/png")
    assert metadata["url"] == "/api/v1/image/jobs/7/outputs/1"
    assert output_path(tmp_path, 7, metadata).read_bytes() == PNG

    with pytest.raises(AppError, match="Signature"):
        save_output(tmp_path, 7, 2, b"not-an-image", "image/png")
    with pytest.raises(AppError, match="volumineuse"):
        save_output(tmp_path, 7, 2, b"x" * (MAX_OUTPUT_BYTES + 1), "image/png")
    with pytest.raises(AppError, match="Nom de sortie"):
        output_path(tmp_path, 7, {"name": "../evil.png"})
    with pytest.raises(ValueError, match="workflow_id"):
        load_workflow("../reference")


@pytest.mark.asyncio
async def test_database_write_accepts_kwargs_for_image_jobs(tmp_path):
    db = Database(tmp_path / "orchestrator.db")
    await db.start(MIGRATIONS)
    job_id = await db.write(create_job, kind="image", provider="comfyui", input_data={"workflow": {"1": {}}})
    state = await db.read(lambda con, value: con.execute("SELECT kind, provider FROM jobs WHERE id=?", (value,)).fetchone(), job_id)
    assert tuple(state) == ("image", "comfyui")
    await db.close()


def test_image_routes_validate_workflows_and_jobs(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EXPOSE_HOST", "127.0.0.1")
    with TestClient(app) as client:
        assert client.get("/api/v1/image/workflows").json() == [{"id": "reference", "label": "Reference SDXL"}]
        assert client.post("/api/v1/image/jobs", json={"workflow_id": "missing", "prompt": "test"}).status_code == 404
        assert client.post("/api/v1/image/jobs", json={"workflow_id": "reference", "prompt": ""}).status_code == 422

        created = client.post("/api/v1/image/jobs", json={"workflow_id": "reference", "prompt": "un chat"}).json()
        job_id = created["job_id"]
        outputs = client.get(f"/api/v1/image/jobs/{job_id}/outputs").json()
        assert outputs["job_id"] == job_id
        assert outputs["outputs"] == []
        assert client.get(f"/api/v1/image/jobs/{job_id}/outputs/1").status_code == 404
        assert client.get("/api/v1/image/jobs/999999/outputs").status_code == 404


@pytest.mark.asyncio
async def test_image_job_saves_controlled_output(tmp_path):
    class FakeImageProvider:
        def estimate(self, options):
            return {"ram_mb": 10, "vram_mb": 0}

        async def run_image(self, workflow, job_id):
            yield ProviderEvent("output", {"filename": "result.png", "type": "output"})
            yield ProviderEvent("completed", {"prompt_id": "fake"})

        async def download_output(self, output):
            return PNG, "image/png"

        async def cancel(self, job_id):
            return None

    db = Database(tmp_path / "orchestrator.db")
    await db.start(MIGRATIONS)
    job_id = await db.write(lambda con: create_job(con, kind="image", provider="comfyui", input_data={"workflow": {"1": {}}}))
    manager = JobManager(db, {"comfyui": FakeImageProvider()}, ResourceManager({"ram_mb": 100, "vram_mb": 0}), data_dir=tmp_path)
    await manager.start()
    await manager.enqueue()
    for _ in range(100):
        state = await db.read(lambda con, value: con.execute("SELECT state FROM jobs WHERE id=?", (value,)).fetchone()[0], job_id)
        if state == "completed":
            break
        await asyncio.sleep(0.01)
    job = await db.read(lambda con, value: dict(con.execute("SELECT state, output FROM jobs WHERE id=?", (value,)).fetchone()), job_id)
    metadata = json.loads(job["output"])[0]

    assert job["state"] == "completed"
    assert output_path(tmp_path, job_id, metadata).read_bytes() == PNG
    await manager.stop()
    await db.close()
