import asyncio

import pytest

from app.errors import AppError
from app.orchestrator.resource_manager import ResourceManager
from app.providers.fake import FakeProvider


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


@pytest.mark.asyncio
async def test_cancelling_a_waiting_job_wakes_it():
    manager = ResourceManager({"ram_mb": 100, "vram_mb": 0})
    first = await manager.acquire({"id": 1, "estimate": {"ram_mb": 100, "vram_mb": 0}})
    waiting = asyncio.create_task(manager.acquire({"id": 2, "estimate": {"ram_mb": 100, "vram_mb": 0}}))
    await asyncio.sleep(0)
    await manager.cancel(2)
    with pytest.raises(AppError, match="annulé"):
        await asyncio.wait_for(waiting, 1)
    await manager.release(first)


@pytest.mark.asyncio
async def test_lru_eviction_never_evicts_pinned_models():
    provider = FakeProvider()
    manager = ResourceManager({"ram_mb": 100, "vram_mb": 0}, {"fake": provider})
    await manager.sync_loaded("fake", [{"name": "old", "size_mb": 60}, {"name": "pinned", "size_mb": 40}])
    loaded = await manager.loaded()
    for item in loaded:
        if item["name"] == "pinned":
            item["pinned"] = True
    # Pinning is an in-memory state; use the public operation for the real cache.
    await manager.set_pinned("fake", "pinned", True)
    reservation = await manager.acquire({"id": 4, "provider": "fake", "model_name": "new", "estimate": {"ram_mb": 50, "vram_mb": 0}})
    await manager.release(reservation)
    assert provider.unloaded_calls == ["old"]
    assert {item["name"] for item in await manager.loaded()} == {"pinned", "new"}


@pytest.mark.asyncio
async def test_pinned_models_can_make_a_job_unavailable():
    provider = FakeProvider()
    manager = ResourceManager({"ram_mb": 100, "vram_mb": 0}, {"fake": provider})
    await manager.sync_loaded("fake", [{"name": "pinned", "size_mb": 90}])
    await manager.set_pinned("fake", "pinned", True)
    with pytest.raises(AppError, match="épinglés"):
        await manager.acquire({"id": 5, "provider": "fake", "model_name": "new", "estimate": {"ram_mb": 20, "vram_mb": 0}})
