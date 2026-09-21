import asyncio
from typing import Any, AsyncIterator

import httpx

from app.errors import AppError

from .base import ProviderEvent


class ComfyUIProvider:
    """HTTP adapter for ComfyUI's prompt, history and view endpoints."""

    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None, poll_interval: float = 0.5, max_outputs: int = 8):
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.AsyncClient(base_url=self.base_url, timeout=httpx.Timeout(10.0, read=30.0))
        if client is not None and str(self.client.base_url) in ("", "/"):
            self.client.base_url = httpx.URL(self.base_url)
        self._owned_client = client is None
        self.poll_interval = poll_interval
        self.max_outputs = max_outputs
        self._prompt_ids: dict[int, str] = {}
        self._slot = asyncio.Semaphore(1)

    async def close(self) -> None:
        if self._owned_client:
            await self.client.aclose()

    async def health(self) -> bool:
        try:
            response = await self.client.get("/system_stats")
            return response.is_success
        except httpx.HTTPError:
            return False

    async def loaded_models(self) -> list[dict[str, Any]]:
        return []

    def estimate(self, options: dict[str, Any] | None = None) -> dict[str, int | str]:
        return {"ram_mb": 512, "vram_mb": 512, "mode": "image", "total_mb": 1024}

    async def run_image(self, workflow: dict[str, Any], job_id: int) -> AsyncIterator[ProviderEvent]:
        async with self._slot:
            client_id = f"orchestrator-{job_id}"
            response = await self.client.post("/prompt", json={"prompt": workflow, "client_id": client_id})
            response.raise_for_status()
            payload = response.json()
            if payload.get("node_errors"):
                raise AppError("provider_error", "ComfyUI a rejeté le workflow", 502, {"node_errors": payload["node_errors"]})
            prompt_id = payload.get("prompt_id")
            if not prompt_id:
                raise AppError("provider_error", "ComfyUI n'a pas renvoyé de prompt_id", 502)
            self._prompt_ids[job_id] = str(prompt_id)
            yield ProviderEvent("progress", {"status": "queued", "prompt_id": prompt_id})
            try:
                while True:
                    response = await self.client.get(f"/history/{prompt_id}")
                    response.raise_for_status()
                    history = response.json().get(str(prompt_id), {})
                    status = history.get("status") or {}
                    status_name = status.get("status_str")
                    if status_name in {"error", "failed"}:
                        raise AppError("provider_error", "ComfyUI a échoué pendant la génération", 502, {"status": status})
                    outputs = history.get("outputs") or {}
                    if status.get("completed") or outputs:
                        output_count = 0
                        for node_id, node_output in outputs.items():
                            for image in node_output.get("images", []):
                                output_count += 1
                                if output_count > self.max_outputs:
                                    raise AppError("output_limit", "ComfyUI a produit trop de sorties", 502)
                                yield ProviderEvent("output", {"node_id": node_id, **image})
                        yield ProviderEvent("completed", {"prompt_id": prompt_id})
                        return
                    yield ProviderEvent("progress", {"status": status_name or "running", "prompt_id": prompt_id})
                    await asyncio.sleep(self.poll_interval)
            finally:
                self._prompt_ids.pop(job_id, None)

    async def download_output(self, output: dict[str, Any]) -> tuple[bytes, str]:
        filename = str(output.get("filename", ""))
        subfolder = str(output.get("subfolder", ""))
        output_type = str(output.get("type", "output"))
        if not filename or "\x00" in filename or "/" in filename or "\\" in filename:
            raise AppError("provider_error", "Nom de sortie ComfyUI invalide", 502)
        normalized_subfolder = subfolder.replace("\\", "/")
        if output_type != "output" or normalized_subfolder.startswith("/") or ".." in normalized_subfolder.split("/"):
            raise AppError("provider_error", "Chemin de sortie ComfyUI invalide", 502)
        response = await self.client.get("/view", params={"filename": filename, "subfolder": subfolder, "type": output_type})
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if content_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise AppError("provider_error", "Type de sortie image non autorisé", 502)
        return response.content, content_type

    async def cancel(self, job_id: int) -> None:
        if job_id not in self._prompt_ids:
            return
        response = await self.client.post("/interrupt")
        response.raise_for_status()
