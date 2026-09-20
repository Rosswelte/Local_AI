import asyncio
from typing import Any, AsyncIterator

from .base import AIProvider, ProviderEvent


class FakeProvider(AIProvider):
    def __init__(self, text: str = "Réponse simulée.", delay: float = 0.001, memory: dict[str, int] | None = None, fail: bool = False):
        self.text = text
        self.delay = delay
        self.memory = memory or {"ram_mb": 100, "vram_mb": 0}
        self.fail = fail
        self.cancelled: set[int] = set()
        self.loaded_calls: list[tuple[str, bool]] = []
        self.unloaded_calls: list[str] = []

    async def health(self) -> bool:
        return True

    async def loaded_models(self) -> list[dict[str, Any]]:
        return []

    def estimate(self, model: dict[str, Any], options: dict[str, Any] | None = None) -> dict[str, int | str]:
        return {**self.memory, "mode": "cpu", "total_mb": self.memory.get("ram_mb", 0) + self.memory.get("vram_mb", 0)}

    async def run(self, model: dict[str, Any], messages: list[dict[str, str]], options: dict[str, Any] | None = None) -> AsyncIterator[ProviderEvent]:
        job_id = int((options or {}).get("job_id", 0))
        if self.fail:
            raise RuntimeError("fake provider failure")
        for index, token in enumerate(self.text.split(" "), 1):
            await asyncio.sleep(self.delay)
            if job_id in self.cancelled:
                return
            yield ProviderEvent("token", token + (" " if index < len(self.text.split(" ")) else ""), index)
            yield ProviderEvent("progress", min(99, index * 100 // max(1, len(self.text.split(" ")))), index)
        yield ProviderEvent("completed", None, len(self.text.split(" ")) + 1)

    async def cancel(self, job_id: int) -> None:
        self.cancelled.add(job_id)

    async def load(self, name: str, pinned: bool = False) -> None:
        self.loaded_calls.append((name, pinned))

    async def unload(self, name: str) -> None:
        self.unloaded_calls.append(name)
