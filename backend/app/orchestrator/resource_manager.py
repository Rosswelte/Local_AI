import asyncio
from dataclasses import dataclass
from typing import Any

from app.errors import AppError


@dataclass
class Reservation:
    job_id: int
    ram_mb: int
    vram_mb: int


class ResourceManager:
    """Minimal single-worker reservation manager; provider calls stay outside the lock."""

    def __init__(self, budgets: dict[str, int], providers: dict[str, Any] | None = None):
        self.budgets = budgets
        self.providers = providers or {}
        self._condition = asyncio.Condition()
        self._reserved = {"ram_mb": 0, "vram_mb": 0}
        self._reservations: dict[int, Reservation] = {}
        self._cancelled: set[int] = set()

    async def update_budgets(self, budgets: dict[str, int]) -> None:
        async with self._condition:
            self.budgets = budgets
            self._condition.notify_all()

    async def cancel(self, job_id: int) -> None:
        async with self._condition:
            self._cancelled.add(job_id)
            self._condition.notify_all()

    async def acquire(self, job: dict[str, Any]) -> Reservation:
        job_id = int(job["id"])
        estimate = job.get("estimate", {})
        ram = int(estimate.get("ram_mb", 0))
        vram = int(estimate.get("vram_mb", 0))
        if ram > self.budgets.get("ram_mb", 0) or vram > self.budgets.get("vram_mb", 0):
            raise AppError("fit_impossible", "Le job ne tient pas dans le budget mémoire", 409, {"missing_mb": max(ram - self.budgets.get("ram_mb", 0), vram - self.budgets.get("vram_mb", 0), 0)})
        if job.get("compatibility") == "not_recommended" and not job.get("force"):
            raise AppError("confirmation_required", "Ce modèle nécessite une confirmation", 409)
        async with self._condition:
            if job_id in self._cancelled:
                self._cancelled.discard(job_id)
                raise AppError("cancelled", "Le job a été annulé", 409)
            while self._reserved["ram_mb"] + ram > self.budgets.get("ram_mb", 0) or self._reserved["vram_mb"] + vram > self.budgets.get("vram_mb", 0):
                await self._condition.wait()
                if job_id in self._cancelled:
                    self._cancelled.discard(job_id)
                    raise AppError("cancelled", "Le job a été annulé", 409)
            self._reserved["ram_mb"] += ram
            self._reserved["vram_mb"] += vram
            reservation = Reservation(job_id, ram, vram)
            self._reservations[reservation.job_id] = reservation
            return reservation

    async def release(self, reservation: Reservation) -> None:
        async with self._condition:
            if self._reservations.pop(reservation.job_id, None) is not None:
                self._cancelled.discard(reservation.job_id)
                self._reserved["ram_mb"] -= reservation.ram_mb
                self._reserved["vram_mb"] -= reservation.vram_mb
                self._condition.notify_all()
