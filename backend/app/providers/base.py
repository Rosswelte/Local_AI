import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator


@dataclass
class ProviderEvent:
    type: str
    data: Any = None
    id: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "type": self.type, "data": self.data}


class AIProvider(ABC):
    @abstractmethod
    async def health(self) -> bool: ...

    @abstractmethod
    async def loaded_models(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    def estimate(self, model: dict[str, Any], options: dict[str, Any] | None = None) -> dict[str, int | str]: ...

    def fit_memory(self, model: dict[str, Any], budgets: dict[str, int], options: dict[str, Any] | None = None) -> bool:
        estimate = self.estimate(model, options)
        return int(estimate["ram_mb"]) <= budgets.get("ram_mb", 0) and int(estimate["vram_mb"]) <= budgets.get("vram_mb", 0)

    @abstractmethod
    async def run(self, model: dict[str, Any], messages: list[dict[str, str]], options: dict[str, Any] | None = None) -> AsyncIterator[ProviderEvent]: ...

    async def cancel(self, job_id: int) -> None:
        return None

    async def load(self, name: str, pinned: bool = False) -> None:
        return None

    async def unload(self, name: str) -> None:
        return None
