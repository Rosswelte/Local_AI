import asyncio

import pytest

from app.errors import AppError
from app.orchestrator.resource_manager import ResourceManager


@pytest.mark.asyncio
async def test_only_one_job_uses_limited_memory():
    manager = ResourceManager({"ram_mb": 100, "vram_mb": 0})
    first = await manager.acquire({"id": 1, "estimate": {"ram_mb": 100, "vram_mb": 0}})
    waiting = asyncio.create_task(manager.acquire({"id": 2, "estimate": {"ram_mb": 100, "vram_mb": 0}}))
    await asyncio.sleep(0)
    assert not waiting.done()
    await manager.release(first)
    second = await asyncio.wait_for(waiting, 1)
    await manager.release(second)


@pytest.mark.asyncio
async def test_fit_and_confirmation_errors():
    manager = ResourceManager({"ram_mb": 100, "vram_mb": 0})
    with pytest.raises(AppError, match="budget"):
        await manager.acquire({"id": 1, "estimate": {"ram_mb": 101, "vram_mb": 0}})
    with pytest.raises(AppError, match="confirmation"):
        await manager.acquire({"id": 2, "estimate": {"ram_mb": 1, "vram_mb": 0}, "compatibility": "not_recommended"})
    reservation = await manager.acquire({"id": 3, "estimate": {"ram_mb": 1, "vram_mb": 0}, "compatibility": "not_recommended", "force": True})
    await manager.release(reservation)
