import json
from typing import Any, AsyncIterator

import httpx

from .base import AIProvider, ProviderEvent


class OllamaProvider(AIProvider):
    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None):
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.AsyncClient(base_url=self.base_url, timeout=httpx.Timeout(10.0, read=None))
        if client is not None and str(self.client.base_url) in ("", "/"):
            self.client.base_url = httpx.URL(self.base_url)
        self._owned_client = client is None

    async def close(self) -> None:
        if self._owned_client:
            await self.client.aclose()

    async def health(self) -> bool:
        try:
            response = await self.client.get("/api/tags")
            return response.is_success
        except httpx.HTTPError:
            return False

    async def installed_models(self) -> list[dict[str, Any]]:
        response = await self.client.get("/api/tags")
        response.raise_for_status()
        return response.json().get("models", [])

    async def loaded_models(self) -> list[dict[str, Any]]:
        response = await self.client.get("/api/ps")
        response.raise_for_status()
        return response.json().get("models", [])

    def estimate(self, model: dict[str, Any], options: dict[str, Any] | None = None) -> dict[str, int | str]:
        options = options or {}
        context = int(options.get("num_ctx", model.get("context_tokens", 4096)))
        cache = max(64, context // 8)
        ram = int(model.get("ram_min_mb", model.get("size_mb", 0))) + cache
        vram = int(model.get("vram_min_mb", 0)) if options.get("mode") == "gpu" else 0
        return {"ram_mb": ram, "vram_mb": vram, "mode": "mixed" if vram else "cpu", "total_mb": ram + vram}

    async def load(self, name: str, pinned: bool = False) -> None:
        response = await self.client.post("/api/generate", json={"model": name, "prompt": "", "stream": False, "keep_alive": -1 if pinned else "5m"})
        response.raise_for_status()

    async def unload(self, name: str) -> None:
        response = await self.client.post("/api/generate", json={"model": name, "prompt": "", "stream": False, "keep_alive": 0})
        response.raise_for_status()

    async def delete(self, name: str) -> None:
        response = await self.client.request("DELETE", "/api/delete", json={"name": name})
        response.raise_for_status()

    async def pull(self, name: str) -> AsyncIterator[ProviderEvent]:
        sequence = 0
        async with self.client.stream("POST", "/api/pull", json={"name": name, "stream": True}) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line:
                    continue
                sequence += 1
                item = json.loads(line)
                if item.get("total"):
                    yield ProviderEvent("progress", {"status": item.get("status"), "completed": item.get("completed", 0), "total": item["total"]}, sequence)
                elif item.get("status"):
                    yield ProviderEvent("progress", {"status": item["status"]}, sequence)
            yield ProviderEvent("completed", None, sequence + 1)

    async def run(self, model: dict[str, Any], messages: list[dict[str, str]], options: dict[str, Any] | None = None) -> AsyncIterator[ProviderEvent]:
        options = options or {}
        sequence = 0
        payload = {"model": model["name"], "messages": messages, "stream": True, "options": {"num_ctx": int(options.get("num_ctx", model.get("context_tokens", 4096)))}}
        async with self.client.stream("POST", "/api/chat", json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line:
                    continue
                item = json.loads(line)
                sequence += 1
                content = item.get("message", {}).get("content")
                if content:
                    yield ProviderEvent("token", content, sequence)
                if item.get("done"):
                    yield ProviderEvent("completed", item, sequence)
