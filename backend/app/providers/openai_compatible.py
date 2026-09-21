import asyncio
import json
from typing import Any, AsyncIterator

import httpx

from .base import AIProvider, ProviderEvent


FORBIDDEN_HEADERS = {"host", "content-length", "transfer-encoding"}


class OpenAICompatibleProvider(AIProvider):
    def __init__(self, base_url: str, api_key: str, model_name: str, extra_headers: dict[str, str] | None = None, client: httpx.AsyncClient | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model_name = model_name
        self.extra_headers = validate_headers(extra_headers or {}, api_key)
        headers = {"Authorization": f"Bearer {api_key}", **self.extra_headers}
        self.client = client or httpx.AsyncClient(base_url=self.base_url, headers=headers, timeout=httpx.Timeout(10.0, read=None))
        if client is not None:
            if str(self.client.base_url) in ("", "/"):
                self.client.base_url = httpx.URL(self.base_url)
            self.client.headers.update(headers)
        self._active: dict[int, asyncio.Task] = {}
        self._owned_client = client is None

    async def close(self) -> None:
        if self._owned_client:
            await self.client.aclose()

    async def health(self) -> bool:
        response = await self.client.get("/models")
        return response.is_success

    async def loaded_models(self) -> list[dict[str, Any]]:
        return []

    def estimate(self, model: dict[str, Any], options: dict[str, Any] | None = None) -> dict[str, int | str]:
        return {"ram_mb": 0, "vram_mb": 0, "mode": "remote", "total_mb": 0}

    async def run(self, model: dict[str, Any], messages: list[dict[str, str]], options: dict[str, Any] | None = None) -> AsyncIterator[ProviderEvent]:
        options = options or {}
        job_id = int(options.get("job_id", 0))
        payload = {"model": model.get("name", self.model_name), "messages": messages, "stream": True}
        if "temperature" in options:
            payload["temperature"] = options["temperature"]
        current = asyncio.current_task()
        if current:
            self._active[job_id] = current
        sequence = 0
        try:
            async with self.client.stream("POST", "/chat/completions", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line or line.startswith(":"):
                        continue
                    raw = line[5:].strip() if line.startswith("data:") else line
                    if raw == "[DONE]":
                        sequence += 1
                        yield ProviderEvent("completed", None, sequence)
                        break
                    item = json.loads(raw)
                    content = item.get("choices", [{}])[0].get("delta", {}).get("content")
                    if content:
                        sequence += 1
                        yield ProviderEvent("token", content, sequence)
        finally:
            self._active.pop(job_id, None)

    async def cancel(self, job_id: int) -> None:
        task = self._active.get(job_id)
        if task:
            task.cancel()

    async def load(self, name: str, pinned: bool = False) -> None:
        return None

    async def unload(self, name: str) -> None:
        return None


def validate_headers(headers: dict[str, str], api_key: str) -> dict[str, str]:
    if len(headers) > 20:
        raise ValueError("too many custom headers")
    result = {}
    for name, value in headers.items():
        if name.lower() in FORBIDDEN_HEADERS:
            raise ValueError(f"header {name} is not allowed")
        if len(name) > 128 or len(value) > 2048:
            raise ValueError("custom header is too large")
        result[name] = value.replace("{api_key}", api_key)
    return result
