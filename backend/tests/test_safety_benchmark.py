from types import SimpleNamespace

import pytest

from app.orchestrator.memory_guard import MemoryGuard
from app.providers.fake import FakeProvider
from app.services.benchmark import measure_provider


def test_memory_guard_detects_low_free_memory():
    guard = MemoryGuard(5, lambda: SimpleNamespace(available=4, total=100))
    assert guard.free_percent() == 4
    assert guard.should_stop()


@pytest.mark.asyncio
async def test_fake_provider_benchmark_is_measured():
    provider = FakeProvider(text="un deux trois", delay=0.001)
    result = await measure_provider(provider, {"id": 1, "name": "fake"})
    assert result["tokens"] >= 3
    assert result["tokens_per_s"] > 0
    assert result["source"] == "measured"
