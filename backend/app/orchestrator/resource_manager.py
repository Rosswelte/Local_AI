import asyncio
import time
from dataclasses import dataclass
from typing import Any

from app.errors import AppError


@dataclass
class LoadedModel:
    provider: str
    name: str
    ram_mb: int
    vram_mb: int
    pinned: bool = False
    last_used: float = 0.0
    active_users: int = 0


@dataclass
class Reservation:
    job_id: int
    ram_mb: int
    vram_mb: int
    model_key: tuple[str, str] | None = None


class ResourceManager:
    """Tracks loaded models and reservations without awaiting providers under its lock."""

    def __init__(self, budgets: dict[str, int], providers: dict[str, Any] | None = None):
        self.budgets = budgets
        self.providers = providers or {}
        self._condition = asyncio.Condition()
        self._reserved = {"ram_mb": 0, "vram_mb": 0}
        self._reservations: dict[int, Reservation] = {}
        self._loaded: dict[tuple[str, str], LoadedModel] = {}
        self._loading: set[tuple[str, str]] = set()
        self._cancelled: set[int] = set()

    async def update_budgets(self, budgets: dict[str, int]) -> None:
        async with self._condition:
            self.budgets = budgets
            self._condition.notify_all()

    async def cancel(self, job_id: int) -> None:
        async with self._condition:
            self._cancelled.add(job_id)
            self._condition.notify_all()

    async def sync_loaded(self, provider_name: str, models: list[dict[str, Any]]) -> None:
        async with self._condition:
            for key in [key for key in self._loaded if key[0] == provider_name]:
                self._loaded.pop(key, None)
            for model in models:
                name = model.get("name") or model.get("model")
                if not name:
                    continue
                vram = _mb(model.get("size_vram", model.get("vram_mb", 0)))
                total = _mb(model.get("size", model.get("size_mb", 0)))
                self._loaded[(provider_name, name)] = LoadedModel(
                    provider_name,
                    name,
                    max(total - vram, 0),
                    vram,
                    last_used=time.monotonic(),
                )
            self._condition.notify_all()

    async def loaded(self) -> list[dict[str, Any]]:
        async with self._condition:
            return [
                {"provider": item.provider, "name": item.name, "ram_mb": item.ram_mb, "vram_mb": item.vram_mb,
                 "pinned": item.pinned, "last_used": item.last_used, "active_users": item.active_users}
                for item in self._loaded.values()
            ]

    async def acquire(self, job: dict[str, Any]) -> Reservation:
        job_id = int(job["id"])
        estimate = job.get("estimate", {})
        requested = {"ram_mb": int(estimate.get("ram_mb", 0)), "vram_mb": int(estimate.get("vram_mb", 0))}
        if requested["ram_mb"] > self.budgets.get("ram_mb", 0) or requested["vram_mb"] > self.budgets.get("vram_mb", 0):
            raise AppError("fit_impossible", "Le job ne tient pas dans le budget mémoire", 409, {"missing_mb": max(requested["ram_mb"] - self.budgets.get("ram_mb", 0), requested["vram_mb"] - self.budgets.get("vram_mb", 0), 0)})
        if job.get("compatibility") == "not_recommended" and not job.get("force"):
            raise AppError("confirmation_required", "Ce modèle nécessite une confirmation", 409)

        key = (str(job.get("provider", "")), str(job.get("model_name", "")))
        track_model = bool(key[0] and key[1] and key[0] in self.providers)
        while True:
            evictions: list[LoadedModel] = []
            async with self._condition:
                if job_id in self._cancelled:
                    self._cancelled.discard(job_id)
                    raise AppError("cancelled", "Le job a été annulé", 409)
                current = self._loaded.get(key) if track_model else None
                needed = {"ram_mb": 0, "vram_mb": 0} if current else requested
                used = self._used_memory()
                if _fits(used, needed, self.budgets):
                    self._reserved["ram_mb"] += needed["ram_mb"]
                    self._reserved["vram_mb"] += needed["vram_mb"]
                    if current:
                        current.active_users += 1
                        current.last_used = time.monotonic()
                    else:
                        if track_model:
                            self._loading.add(key)
                    reservation = Reservation(job_id, needed["ram_mb"], needed["vram_mb"], key if track_model else None)
                    self._reservations[job_id] = reservation
                    break
                pinned = {
                    "ram_mb": sum(item.ram_mb for item in self._loaded.values() if item.pinned),
                    "vram_mb": sum(item.vram_mb for item in self._loaded.values() if item.pinned),
                }
                if not _fits(pinned, needed, self.budgets):
                    raise AppError("resource_unavailable", "Les modèles épinglés occupent le budget nécessaire", 503, {"pinned": [item.name for item in self._loaded.values() if item.pinned]})
                evictions = self._eviction_candidates(key, needed)
                if evictions:
                    for item in evictions:
                        self._loaded.pop((item.provider, item.name), None)
                else:
                    await self._condition.wait()
                    continue
            for item in evictions:
                try:
                    await self.providers[item.provider].unload(item.name)
                except Exception:
                    async with self._condition:
                        self._loaded[(item.provider, item.name)] = item
                        self._condition.notify_all()
                    raise AppError("provider_unreachable", f"Impossible de décharger {item.name}", 502)
            continue

        if track_model and key in self._loading:
            try:
                await self.providers[key[0]].load(key[1], bool(job.get("pinned", False)))
            except Exception:
                async with self._condition:
                    self._loading.discard(key)
                await self.release(reservation)
                raise AppError("provider_unreachable", f"Impossible de charger {key[1]}", 502)
            async with self._condition:
                self._loading.discard(key)
                self._reserved["ram_mb"] -= reservation.ram_mb
                self._reserved["vram_mb"] -= reservation.vram_mb
                self._loaded[key] = LoadedModel(key[0], key[1], requested["ram_mb"], requested["vram_mb"], bool(job.get("pinned", False)), time.monotonic(), 1)
                reservation.ram_mb = 0
                reservation.vram_mb = 0
                self._condition.notify_all()
        return reservation

    async def release(self, reservation: Reservation) -> None:
        async with self._condition:
            self._cancelled.discard(reservation.job_id)
            if self._reservations.pop(reservation.job_id, None) is not None:
                self._reserved["ram_mb"] -= reservation.ram_mb
                self._reserved["vram_mb"] -= reservation.vram_mb
                if reservation.model_key in self._loaded:
                    item = self._loaded[reservation.model_key]
                    item.active_users = max(0, item.active_users - 1)
                    item.last_used = time.monotonic()
                self._condition.notify_all()

    async def unload(self, provider_name: str, model_name: str) -> None:
        key = (provider_name, model_name)
        async with self._condition:
            item = self._loaded.get(key)
            if not item:
                return
            if item.pinned:
                raise AppError("model_pinned", "Le modèle est épinglé", 409)
            if item.active_users:
                raise AppError("resource_busy", "Le modèle est utilisé", 409)
            self._loaded.pop(key)
        try:
            await self.providers[provider_name].unload(model_name)
        except Exception:
            async with self._condition:
                self._loaded[key] = item
                self._condition.notify_all()
            raise AppError("provider_unreachable", f"Impossible de décharger {model_name}", 502)
        async with self._condition:
            self._condition.notify_all()

    async def set_pinned(self, provider_name: str, model_name: str, pinned: bool) -> None:
        key = (provider_name, model_name)
        async with self._condition:
            item = self._loaded.get(key)
            if item:
                item.pinned = pinned
                item.last_used = time.monotonic()
            self._condition.notify_all()
        if item and pinned:
            await self.providers[provider_name].load(model_name, True)

    async def load_model(self, provider_name: str, model: dict[str, Any], estimate: dict[str, Any], pinned: bool = False) -> None:
        key = (provider_name, model["name"])
        async with self._condition:
            current = self._loaded.get(key)
            if current:
                current.pinned = pinned
                current.last_used = time.monotonic()
                return
        reservation = await self.acquire({
            "id": -max(1, int(model.get("id", 1))),
            "provider": provider_name,
            "model_name": model["name"],
            "estimate": estimate,
            "pinned": pinned,
        })
        await self.release(reservation)

    def _used_memory(self) -> dict[str, int]:
        return {
            "ram_mb": self._reserved["ram_mb"] + sum(item.ram_mb for item in self._loaded.values()),
            "vram_mb": self._reserved["vram_mb"] + sum(item.vram_mb for item in self._loaded.values()),
        }

    def _eviction_candidates(self, key: tuple[str, str], needed: dict[str, int]) -> list[LoadedModel]:
        used = self._used_memory()
        candidates = sorted((item for item in self._loaded.values() if (item.provider, item.name) != key and not item.pinned and item.active_users == 0), key=lambda item: item.last_used)
        selected = []
        for item in candidates:
            selected.append(item)
            used["ram_mb"] -= item.ram_mb
            used["vram_mb"] -= item.vram_mb
            if _fits(used, needed, self.budgets):
                return selected
        return []


def _fits(used: dict[str, int], needed: dict[str, int], budgets: dict[str, int]) -> bool:
    return used["ram_mb"] + needed["ram_mb"] <= budgets.get("ram_mb", 0) and used["vram_mb"] + needed["vram_mb"] <= budgets.get("vram_mb", 0)


def _mb(value: Any) -> int:
    value = int(value or 0)
    return value // (1024 * 1024) if value > 1_000_000 else value
